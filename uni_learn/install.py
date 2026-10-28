"""Read-only plans and ownership-aware, reversible file installation."""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import re
import shlex
import sys
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath
from . import __version__
from .adapters import render, load_registry
from .budget import counter_for_model, measure
from .contracts import ContractError, canonical
from .package import build_bytes, read_json
from .paths import home_directory, roots, safe_id
from .state import ConflictError, atomic_json, locked


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _no_links(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ContractError("symlink in installation path: " + str(candidate))


def _safe_target(root, relative):
    if (not isinstance(relative, str) or not relative or relative == "." or
            Path(relative).is_absolute() or PureWindowsPath(relative).drive or
            "\\" in relative or ".." in Path(relative).parts):
        raise ContractError("unsafe installation path")
    root = Path(root).expanduser().resolve()
    path = root / relative
    _no_links(path)
    return path


def _observed(path):
    _no_links(path)
    if not path.exists():
        return {"exists": False, "sha256": None}
    if not path.is_file():
        raise ContractError("installation target is not a file")
    return {"exists": True, "sha256": _hash(path.read_bytes())}


def _record(path):
    if not _observed(path)["exists"]:
        return None
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("owned"), list):
        raise ContractError("invalid ownership record")
    seen = set()
    for entry in value["owned"]:
        if not isinstance(entry, dict) or not {"path", "sha256"} <= set(entry):
            raise ContractError("invalid owned entry")
        if entry["path"] in seen:
            raise ContractError("duplicate owned path")
        seen.add(entry["path"])
        digest = entry["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ContractError("invalid owned hash")
        if entry.get("kind", "file") not in {"file", "block"}:
            raise ContractError("invalid owned entry kind")
        if entry.get("kind") == "block" and (not isinstance(entry.get("block_sha256"), str) or
                 not re.fullmatch(r"[0-9a-f]{64}", entry["block_sha256"])):
            raise ContractError("invalid managed block hash")
        if entry.get("kind") == "block" and entry.get("block_profile", value.get("profile")) != value.get("profile"):
            raise ContractError("managed block profile mismatch")
        if entry.get("kind") == "block" and entry.get("prefix_added", "") not in {"", "\n", "\r\n"}:
            raise ContractError("invalid managed block prefix")
    return value


def _identity(project, profile, generation, *, scope="project", user_home=None, codex_home=None):
    if scope == "project":
        # Preserve receipt identities made by every earlier release.
        source = {"project": str(project), "profile": profile, "generation": generation}
    else:
        source = {"scope": scope, "user_home": str(user_home), "codex_home": str(codex_home),
                  "profile": profile, "generation": generation}
    return _hash(canonical(source))[:24]


def _install_roots(profile, project, scope, user_home, codex_home):
    if scope == "project":
        if project is None:
            raise ContractError("project installation requires --project")
        project = Path(project).expanduser().resolve()
        if not project.is_dir():
            raise ContractError("project directory does not exist")
        return project, project, None, None
    descriptor = read_json(load_registry()["profiles"][profile]["descriptor"])
    if scope != "user" or descriptor.get("installation_scopes", {}).get("user", {}).get("instruction_root") != "codex_home":
        raise ContractError("profile does not support a user installation with Codex home instructions")
    if user_home is not None and not Path(user_home).expanduser().is_absolute():
        raise ContractError("user home must be absolute")
    user_home = home_directory(home=user_home).resolve()
    if not user_home.is_dir():
        raise ContractError("user home directory does not exist")
    configured = codex_home if codex_home is not None else os.environ.get("CODEX_HOME")
    codex_home = Path(configured).expanduser().resolve() if configured else user_home / ".codex"
    _no_links(codex_home)
    if codex_home.exists() and not codex_home.is_dir():
        raise ContractError("Codex home is not a directory")
    # CODEX_HOME/AGENTS.override.md takes precedence when it has content. Do not
    # create an override just to install Uni Learn; that would hide AGENTS.md.
    override = codex_home / "AGENTS.override.md"
    _no_links(override)
    instruction = (override if override.is_file() and override.read_bytes().decode("utf-8").strip()
                   else codex_home / "AGENTS.md")
    return None, user_home, codex_home, instruction


def _artifact_target(artifact, target_root, scope, instruction):
    if scope == "user" and artifact["role"] == "persistent":
        return instruction
    return _safe_target(target_root, artifact["path"])


def _receipt_path(data, ident):
    return data / "receipts" / (safe_id(ident) + ".json")


def _journal_path(data, ident):
    return data / "receipts" / (safe_id(ident) + ".journal.json")


def _archive_resources(archive):
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        return {name: package.read(name) for name in sorted(package.namelist())
                if name.startswith("core/") and not name.endswith("/")}


def _validate_record(path, value, data, *, journal=False):
    ident = safe_id(value.get("install_id", ""))
    expected_path = _journal_path(data, ident) if journal else _receipt_path(data, ident)
    if path != expected_path or value.get("data_root") != str(data):
        raise ContractError("ownership record is outside its application root")
    profile, generation = value.get("profile"), value.get("generation")
    registry = load_registry()["profiles"]
    if profile not in registry:
        raise ContractError("ownership profile unknown")
    descriptor = read_json(registry[profile]["descriptor"])
    scope = value.get("scope", "project")
    if scope == "project":
        project = Path(value.get("project", "")).expanduser()
        if not project.is_absolute() or project != project.resolve() or not project.is_dir():
            raise ContractError("ownership project missing or redirected")
        target_root, codex_home = project, None
    elif scope == "user" and descriptor.get("installation_scopes", {}).get("user", {}).get(
            "instruction_root") == "codex_home":
        user_home = Path(value.get("user_home", "")).expanduser()
        codex_home = Path(value.get("codex_home", "")).expanduser()
        if (not user_home.is_absolute() or not user_home.is_dir() or user_home != user_home.resolve()
                or not codex_home.is_absolute() or codex_home != codex_home.resolve()):
            raise ContractError("ownership user home missing or redirected")
        _no_links(codex_home)
        project, target_root = None, user_home
    else:
        raise ContractError("invalid ownership scope")
    if _identity(project, profile, generation,
            scope=scope, user_home=target_root if scope == "user" else None,
            codex_home=codex_home) != ident:
        raise ContractError("ownership identity mismatch")
    version = value.get("runtime_version", "")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ContractError("invalid release version")
    release = _safe_target(data, "releases/" + version + "/" + ident)
    if scope == "user":
        if "user" not in descriptor.get("installation_scopes", {}):
            raise ContractError("profile does not permit user installation")
        allowed = {_safe_target(target_root, entry["path"]) for entry in descriptor["files"]
                   if entry["mode"] != "persistent"}
        allowed.update({_safe_target(codex_home, "AGENTS.md"),
                        _safe_target(codex_home, "AGENTS.override.md")})
    else:
        allowed = {_safe_target(project, entry["path"]) for entry in descriptor["files"]}
    resource_names = set(_archive_resources(build_bytes()))
    allowed.add(release / "uni-learn.pyz")
    allowed.update(release / "resources" / name for name in resource_names)
    for entry in value["owned"]:
        target = Path(entry["path"])
        if target not in allowed:
            try:
                relative = target.relative_to(data / "releases")
            except ValueError:
                raise ContractError("ownership record contains an unapproved path") from None
            parts = relative.parts
            if (len(parts) < 3 or parts[1] != ident or
                    not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", parts[0]) or
                    (parts[2:] != ("uni-learn.pyz",) and
                     (not parts[2:] or parts[2] != "resources" or "/".join(parts[3:]) not in resource_names))):
                raise ContractError("ownership record contains an unapproved path")
        _no_links(target)
        if scope == "user" and entry.get("kind") == "block" and target.name not in {"AGENTS.md", "AGENTS.override.md"}:
            raise ContractError("managed block outside instruction file")
        if entry.get("staging_path"):
            staging = Path(entry["staging_path"])
            if not journal or staging.parent != target.parent or not staging.name.startswith(".uni-learn-"):
                raise ContractError("unapproved staged path")
            _no_links(staging)
        if entry.get("backup_path"):
            backup = Path(entry["backup_path"])
            if not journal or backup.parent != target.parent or not backup.name.startswith(".uni-learn-"):
                raise ContractError("unapproved backup path")
            _no_links(backup)
    return value


def _marker_content(prior, entry, profile):
    # Native rule frontmatter must stay at byte zero, outside the managed body.
    # An incompatible existing header needs a human merge; silently picking one
    # could change whether the host loads the entire file.
    header = ""
    entry_frontmatter = _frontmatter(entry)
    if entry_frontmatter is not None:
        header, entry = entry_frontmatter
        existing_frontmatter = _frontmatter(prior)
        if existing_frontmatter is not None:
            existing_header, existing_body = existing_frontmatter
            if existing_header.replace("\r\n", "\n") != header.replace("\r\n", "\n"):
                raise ContractError("incompatible existing persistent instruction frontmatter; merge manually")
            header = existing_header
            prior = existing_body
    start, end = "<!-- uni-learn:" + profile + " -->", "<!-- /uni-learn:" + profile + " -->"
    newline = "\r\n" if "\r\n" in header + prior else "\n"
    entry = entry.replace("\r\n", "\n").replace("\n", newline)
    block = start + newline + entry.rstrip() + newline + end + newline
    if start in prior or end in prior:
        if prior.count(start) != 1 or prior.count(end) != 1 or prior.index(end) < prior.index(start):
            raise ContractError("ambiguous existing Uni Learn instruction block")
        old = _managed_block(prior, profile)
        return header + prior[:old[0]] + block + prior[old[1]:]
    return header + prior + (newline if prior and not prior.endswith(("\n", "\r")) else "") + block


def _frontmatter(content):
    """Return an exact frontmatter prefix and body, accepting LF or CRLF."""
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    for index, line in enumerate(lines[1:], 1):
        if line.rstrip("\r\n") == "---":
            return "".join(lines[:index + 1]), "".join(lines[index + 1:])
    raise ContractError("unterminated persistent instruction frontmatter")


def _managed_block(content, profile):
    start, end = "<!-- uni-learn:" + profile + " -->", "<!-- /uni-learn:" + profile + " -->"
    if start not in content and end not in content:
        return None
    if content.count(start) != 1 or content.count(end) != 1:
        raise ContractError("ambiguous existing Uni Learn instruction block")
    first = content.index(start)
    if content.index(end) < first:
        raise ContractError("ambiguous existing Uni Learn instruction block")
    last = content.index(end, first) + len(end)
    if content[last:last + 2] == "\r\n":
        last += 2
    elif last < len(content) and content[last] == "\n":
        last += 1
    return first, last, content[first:last]


def _managed_entry(path, content, profile, *, created=False, prefix_added=""):
    block = _managed_block(content.decode("utf-8"), profile)
    if block is None:
        raise ContractError("managed instruction block missing")
    return {"path": str(path), "sha256": _hash(content), "kind": "block",
            "block_sha256": _hash(block[2].encode("utf-8")), "block_profile": profile,
            "file_created": created, "prefix_added": prefix_added}


def _declared_skill_name(content):
    """Return (safe name, ambiguous syntax) for a small YAML scalar subset."""
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        return None, False
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return None, True
    header_lines = lines[1:closing]
    if any(re.match(r"^(?:['\"]name['\"]|<<)\s*:", line) for line in header_lines):
        return None, True
    declarations = [line.split(":", 1)[1].strip() for line in header_lines
                    if re.match(r"^name\s*:", line)]
    if not declarations:
        return None, True
    if len(declarations) != 1:
        return None, True
    # Folded/literal blocks, escapes, aliases, and other YAML features need a
    # YAML parser to resolve. Refuse those names for manual review instead of
    # accidentally accepting a second canonical invocation.
    simple = r"[A-Za-z0-9][A-Za-z0-9_-]*"
    match = re.fullmatch(rf'(?:"({simple})"|\'({simple})\'|({simple}))(?:\s+#.*)?', declarations[0])
    return (next(value for value in match.groups() if value is not None), False) if match else (None, True)


def _skill_inventory(project, rendered):
    """Find project-local names that would shadow a generated native skill."""
    command_paths = {Path(item["path"]) for item in rendered["artifacts"]
                     if item["role"] == "command" and Path(item["path"]).name == "SKILL.md"}
    roots = sorted({project / path.parent.parent for path in command_paths})
    native_names = set()
    for item in rendered["artifacts"]:
        if item["role"] == "command" and Path(item["path"]).name == "SKILL.md":
            name, ambiguous = _declared_skill_name(item["content"])
            if ambiguous:
                raise ContractError("generated native skill has an ambiguous name")
            native_names.add(name)
    expected = {project / path for path in command_paths}
    inventory, conflicts = [], []
    for root in roots:
        _no_links(root)
        if not root.exists():
            continue
        if not root.is_dir():
            raise ContractError("native skill root is not a directory: " + str(root))
        # Unrelated linked skills are common in user homes. Resolve their declared
        # name for collision detection, while target paths remain non-symlinked.
        for candidate in root.rglob("*"):
            if candidate.is_symlink():
                linked = candidate / "SKILL.md" if candidate.is_dir() else candidate
                if linked.name != "SKILL.md" or not linked.is_file():
                    continue
                name, ambiguous = _declared_skill_name(linked.read_text("utf-8", errors="replace"))
                inventory.append({"path": str(linked), "sha256": _hash(linked.read_bytes()),
                                  "name": name, "ambiguous_name": ambiguous, "linked": True})
                if ambiguous or name in native_names or candidate.name in native_names:
                    conflicts.append({"path": str(linked), "name": name,
                                      "reason": "ambiguous_native_skill_name" if ambiguous else "native_skill_name_collision"})
        for path in sorted(root.rglob("SKILL.md")):
            if path.is_symlink():
                continue
            _no_links(path)
            observed = _observed(path)
            if not observed["exists"]:
                continue
            name, ambiguous = _declared_skill_name(path.read_text("utf-8", errors="replace"))
            inventory.append({"path": str(path), "sha256": observed["sha256"],
                              "name": name, "ambiguous_name": ambiguous})
            if path not in expected and (ambiguous or name in native_names):
                conflicts.append({"path": str(path), "name": name,
                                  "reason": "ambiguous_native_skill_name" if ambiguous else "native_skill_name_collision"})
    return inventory, conflicts


def plan_install(profile, project=None, *, application_roots=None, generation=None, budget_model=None,
                 scope="project", automatic=False, user_home=None, codex_home=None):
    data = Path((application_roots or roots())["data"]).expanduser().resolve()
    project, target_root, codex_home, instruction = _install_roots(
        profile, project, scope, user_home, codex_home)
    if scope == "user":
        user_home = target_root
        descriptor = read_json(load_registry()["profiles"][profile]["descriptor"])
        if "user" not in descriptor.get("installation_scopes", {}):
            raise ContractError("profile does not permit user installation")
    counter, counter_source = counter_for_model(budget_model) if budget_model is not None else (None, None)
    archive = build_bytes()
    ident = _identity(project, profile, generation, scope=scope, user_home=user_home, codex_home=codex_home)
    release = _safe_target(data, "releases/" + __version__ + "/" + ident + "/uni-learn.pyz")
    resource_root = release.parent / "resources"
    context = {"project_root": str(project) if project else None, "project": str(project) if project else None,
               "scope": scope, "profile": profile,
               "schema_version": 1, "version": __version__, "generation": generation,
               "runtime_path": str(release), "resource_root": str(resource_root),
               "runtime_command": (subprocess_command(sys.executable, release))}
    rendered = render(profile, context)
    skill_inventory, skill_conflicts = _skill_inventory(target_root, rendered)
    receipt_path, journal_path = _receipt_path(data, ident), _journal_path(data, ident)
    receipt, journal = _record(receipt_path), _record(journal_path)
    if receipt:
        _validate_record(receipt_path, receipt, data)
    if journal:
        _validate_record(journal_path, journal, data, journal=True)
    owned = {item["path"]: item for item in receipt["owned"]} if receipt else {}
    recovery = {item["path"]: item for item in journal["owned"]} if journal else {}
    specifications = [(release, archive, "runtime", None, False)]
    specifications += [(_safe_target(data, "releases/" + __version__ + "/" + ident + "/resources/" + name),
                        content, "resource", None, False)
                       for name, content in _archive_resources(archive).items()]
    manual = []
    for artifact in rendered["artifacts"]:
        path = _artifact_target(artifact, target_root, scope, instruction)
        content = artifact["content"]
        if artifact["role"] == "persistent":
            prior = path.read_bytes().decode("utf-8") if _observed(path)["exists"] else ""
            content = _marker_content(prior, content, profile)
        specifications.append((path, content.encode("utf-8"), artifact["role"], artifact.get("command"),
                               artifact["role"] == "persistent"))
    actions, contents, committed_recovery, prepared_recovery = [], {}, {}, {}
    for path, content, role, command, persistent in specifications:
        observed = _observed(path)
        desired = _hash(content)
        proven = owned.get(str(path))
        recover = recovery.get(str(path))
        recovered = False
        prepared = False
        if recover and observed["exists"]:
            stat = path.stat()
            same_inode = [stat.st_dev, stat.st_ino] == recover.get("file_id")
            recovered = observed["sha256"] == recover["sha256"] and same_inode
            if not recovered and same_inode and recover.get("kind") == "block":
                current_block = _managed_block(path.read_bytes().decode("utf-8"), profile)
                recovered = bool(current_block and
                                 _hash(current_block[2].encode("utf-8")) == recover["block_sha256"])
            prepared = observed["sha256"] == recover.get("prior_sha256")
            if recovered:
                committed_recovery[str(path)] = recover
                proven = recover
            elif prepared:
                prepared_recovery[str(path)] = recover
        decoded = content.decode("utf-8") if role != "runtime" else ""
        budget = None
        if persistent:
            # Generated entries contain no auto-expanded imports. Unknown user
            # text may, so an exact file count alone cannot certify its context.
            current_text = path.read_bytes().decode("utf-8") if observed["exists"] else ""
            current_block = _managed_block(current_text, profile) if current_text else None
            block_only = bool(current_block and current_text == current_block[2])
            known_entry = (not observed["exists"] or
                           bool(proven and proven.get("kind") != "block") or
                           bool(proven and proven.get("kind") == "block" and
                                 proven.get("file_created") and block_only and observed["sha256"] == proven["sha256"]) or
                           bool(recovered and recover.get("kind") == "block" and
                                 recover.get("file_created") and block_only and observed["sha256"] == recover["sha256"]))
            budget = measure(decoded, exact_counter=counter if known_entry else None)
            if counter is not None:
                budget["counter"] = counter_source
                budget["model_mapping_requires_host_match"] = True
                if not known_entry:
                    budget["file_measurement"] = measure(decoded, exact_counter=counter)
                    budget["reason"] = "existing_user_import_expansion_unverified"
            budget["sha256"] = desired
            if automatic:
                managed = _managed_block(decoded, profile)
                budget["managed_entry_bytes"] = len(managed[2].encode("utf-8")) if managed else None
                if budget["under_1000"] is False:
                    budget["reason"] = "complete_instruction_file_exceeds_1000_for_selected_model"
        manual_entry = persistent and not automatic and not (counter is not None and budget["under_1000"] is True
                                           and (not observed["exists"] or proven or recovered))
        over_budget = bool(automatic and persistent and counter is not None and
                           (budget["under_1000"] is False or
                            budget.get("file_measurement", {}).get("under_1000") is False))
        proven_block = bool(proven and proven.get("kind") == "block")
        block_intact = False
        if proven_block and observed["exists"]:
            existing_block = _managed_block(path.read_bytes().decode("utf-8"), profile)
            block_intact = bool(existing_block and _hash(existing_block[2].encode("utf-8")) == proven["block_sha256"])
        if over_budget:
            action = "conflict_budget_exceeded"
        elif recovered and desired == observed["sha256"]:
            action = "recover_owned"
        elif proven_block and observed["exists"] and not block_intact:
            action = "conflict_owned_drift"
        elif proven and not proven_block and observed["exists"] and observed["sha256"] != proven["sha256"]:
            action = "conflict_owned_drift"
        elif recover and observed["exists"] and not recovered and not prepared:
            action = "conflict_interrupted_drift"
        elif manual_entry:
            action = "manual_patch"
        elif (automatic and persistent and observed["exists"] and not proven and
              _managed_block(path.read_bytes().decode("utf-8"), profile) is not None):
            action = "update_block" if observed["sha256"] != desired else "adopt_block"
        elif automatic and persistent and observed["exists"] and proven_block:
            action = "reuse_owned" if observed["sha256"] == desired else "update_block"
        elif automatic and persistent and observed["exists"]:
            action = "create_block"
        elif observed["exists"] and desired == observed["sha256"]:
            action = "reuse_owned" if proven else "reuse_unowned"
        elif observed["exists"]:
            action = "update_owned" if proven else "conflict_existing"
        else:
            action = "create_owned"
        prior = path.read_bytes().decode("utf-8", errors="replace") if observed["exists"] and role != "runtime" else ""
        diff = "".join(difflib.unified_diff(prior.splitlines(True), decoded.splitlines(True),
                                           fromfile=str(path) + " (current)", tofile=str(path) + " (proposed)"))
        item = {"path": str(path), "role": role, "command": command, "action": action,
                "desired_sha256": desired, "observed": observed, "diff": diff if role != "resource" else None,
                "budget": budget}
        if manual_entry:
            item["manual_content"] = decoded
            manual.append({"path": str(path), "content": decoded,
                           "reason": "persistent_entry_budget_exceeded" if budget["under_1000"] is False else "persistent_entry_budget_unverified",
                           "instructions": "Review the complete proposed entry; preserve existing instructions. This patch is never applied automatically."})
        actions.append(item)
        contents[str(path)] = content
    conflicts = bool(skill_conflicts) or any(item["action"].startswith("conflict") for item in actions)
    public = {"schema_version": 1, "profile": profile, "project": str(project) if project else None,
              "scope": scope, "user_home": str(user_home) if scope == "user" else None,
              "codex_home": str(codex_home) if scope == "user" else None,
              "automatic": automatic, "generation": generation,
              "install_id": ident, "runtime_version": __version__, "runtime_sha256": _hash(archive),
              "data_root": str(data), "receipt": str(receipt_path), "journal": str(journal_path),
              "budget_model": budget_model,
              "receipt_input": _observed(receipt_path), "journal_input": _observed(journal_path),
              "commands": rendered["commands"], "capabilities": rendered["capabilities"],
              "skill_inventory": skill_inventory, "skill_conflicts": skill_conflicts,
              "warnings": rendered["warnings"], "manual_patches": manual, "actions": actions,
              "status": "blocked" if conflicts else "partial" if manual else "ready",
              "integration_status": "partial" if manual else "ready",
              "unresolved_requirements": ["persistent_entry_budget", "automatic_efficiency_activation"] if manual else []}
    public["plan_digest"] = _hash(canonical(public))
    return {"plan": public, "contents": contents, "recovery": committed_recovery,
            "prepared": prepared_recovery}


def subprocess_command(python, archive):
    if os.name == "nt":
        import subprocess
        return subprocess.list2cmdline([str(python), str(archive)])
    return shlex.join([str(python), str(archive)])


def _create_new(path, content, before_commit=None):
    _no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _no_links(path)
    fd, temporary = tempfile.mkstemp(prefix=".uni-learn-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _no_links(path)
        staged = Path(temporary).stat()
        file_id = [staged.st_dev, staged.st_ino]
        if before_commit is not None:
            before_commit(file_id, temporary)
        # A hard link commits the staged file atomically and cannot replace a racing target.
        os.link(temporary, path)
        return file_id
    finally:
        Path(temporary).unlink(missing_ok=True)


def _unchanged(entry):
    path = Path(entry["path"])
    observed = _observed(path)
    if not observed["exists"]:
        return False
    if entry.get("kind") == "block":
        try:
            block = _managed_block(path.read_bytes().decode("utf-8"), entry.get("block_profile", "codex"))
        except (UnicodeError, ContractError):
            return False
        return bool(block and _hash(block[2].encode("utf-8")) == entry["block_sha256"])
    if observed["sha256"] != entry["sha256"]:
        return False
    if "file_id" in entry:
        stat = path.stat()
        return [stat.st_dev, stat.st_ino] == entry["file_id"]
    return True


def _whole_unchanged(entry):
    path = Path(entry["path"])
    if _observed(path)["sha256"] != entry["sha256"]:
        return False
    stat = path.stat()
    return [stat.st_dev, stat.st_ino] == entry.get("file_id")


def _replace_existing(path, content, expected_sha, before_commit=None):
    """Atomically replace a checked file after journaling its staged inode."""
    _no_links(path)
    observed = _observed(path)
    if observed != {"exists": True, "sha256": expected_sha}:
        raise ConflictError("installation input changed: " + str(path))
    old_stat = path.stat()
    fd, temporary = tempfile.mkstemp(prefix=".uni-learn-", dir=path.parent)
    backup = None
    callback_done = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, old_stat.st_mode & 0o777)
        stage_stat = Path(temporary).stat()
        file_id = [stage_stat.st_dev, stage_stat.st_ino]
        if before_commit:
            backup_fd, backup = tempfile.mkstemp(prefix=".uni-learn-", dir=path.parent)
            os.close(backup_fd)
            Path(backup).unlink()
            os.link(path, backup)
            before_commit(file_id, temporary, backup)
            callback_done = True
        if _observed(path)["sha256"] != expected_sha or path.stat().st_ino != old_stat.st_ino:
            raise ConflictError("installation input changed: " + str(path))
        os.replace(temporary, path)
        return file_id
    finally:
        Path(temporary).unlink(missing_ok=True)
        if backup and not callback_done:
            Path(backup).unlink(missing_ok=True)


def apply_install(profile, project, plan_digest, *, application_roots=None, generation=None, budget_model=None,
                  scope="project", automatic=False, user_home=None, codex_home=None, fail_after=None):
    options = {"application_roots": application_roots, "generation": generation, "budget_model": budget_model,
               "scope": scope, "automatic": automatic, "user_home": user_home, "codex_home": codex_home}
    initial = plan_install(profile, project, **options)["plan"]
    if initial["plan_digest"] != plan_digest:
        raise ConflictError("installation plan changed; rerun dry run")
    if initial["status"] == "blocked":
        raise ConflictError("installation has conflicts")
    receipt_path = Path(initial["receipt"])
    with locked(receipt_path.with_suffix(".lock")):
        planned = plan_install(profile, project, **options)
        plan = planned["plan"]
        if plan["plan_digest"] != plan_digest:
            raise ConflictError("installation inputs changed before lock")
        previous = _record(receipt_path)
        owned = {item["path"]: item for item in previous["owned"]} if previous else {}
        owned.update(planned["recovery"])
        created, replaced = [], []
        journal_path = Path(plan["journal"])
        journal = {key: plan[key] for key in ("schema_version", "install_id", "profile", "project",
                                              "scope", "user_home", "codex_home", "generation",
                                              "runtime_version", "data_root")}
        journal_owned = dict(planned["recovery"])
        journal["owned"] = list(journal_owned.values())
        try:
            atomic_json(journal_path, journal)
            for action in plan["actions"]:
                path = Path(action["path"])
                if _observed(path) != action["observed"]:
                    raise ConflictError("installation input changed: " + str(path))
                operation = action["action"]
                if operation == "adopt_block":
                    owned[str(path)] = _managed_entry(path, planned["contents"][str(path)], profile)
                    continue
                if operation in {"reuse_owned", "recover_owned"}:
                    if action["role"] == "persistent" and automatic and str(path) in owned:
                        owned[str(path)] = _managed_entry(path, path.read_bytes(), profile,
                            created=(owned[str(path)].get("file_created", False) and
                                     action["observed"]["sha256"] == owned[str(path)]["sha256"]),
                            prefix_added=owned[str(path)].get("prefix_added", ""))
                    continue
                if operation not in {"create_owned", "create_block", "update_block", "update_owned"}:
                    continue
                old_content = path.read_bytes() if action["observed"]["exists"] else None
                prefix_added = owned.get(str(path), {}).get("prefix_added", "")
                if operation == "create_block" and old_content is not None:
                    old_text = old_content.decode("utf-8")
                    desired_text = planned["contents"][str(path)].decode("utf-8")
                    before_block = desired_text[:_managed_block(desired_text, profile)[0]]
                    if before_block.startswith(old_text):
                        prefix_added = before_block[len(old_text):]
                    if prefix_added not in {"", "\n", "\r\n"}:
                        raise ContractError("unexpected managed block prefix")
                def before_commit(file_id, staging, backup=None):
                    prepared = {"path": str(path), "sha256": action["desired_sha256"],
                                "file_id": file_id, "staging_path": str(staging)}
                    if backup:
                        prepared["backup_path"] = str(backup)
                    if old_content is not None:
                        prepared["prior_sha256"] = action["observed"]["sha256"]
                    if action["role"] == "persistent" and automatic:
                        prepared.update(_managed_entry(path, planned["contents"][str(path)], profile,
                            created=(old_content is None or bool(owned.get(str(path), {}).get("file_created", False) and
                                     action["observed"]["sha256"] == owned[str(path)]["sha256"])),
                            prefix_added=prefix_added))
                        prepared["file_id"] = file_id
                        prepared["staging_path"] = str(staging)
                        if old_content is not None:
                            prepared["prior_sha256"] = action["observed"]["sha256"]
                    journal_owned[str(path)] = prepared
                    journal["owned"] = list(journal_owned.values())
                    atomic_json(journal_path, journal)
                    if old_content is None:
                        created.append(prepared)
                    else:
                        replaced.append((prepared, backup))
                if old_content is None:
                    file_id = _create_new(path, planned["contents"][str(path)], before_commit)
                else:
                    file_id = _replace_existing(path, planned["contents"][str(path)],
                                                action["observed"]["sha256"], before_commit)
                entry = ({"path": str(path), "sha256": action["desired_sha256"], "file_id": file_id}
                         if action["role"] != "persistent" or not automatic else
                         _managed_entry(path, planned["contents"][str(path)], profile,
                                        created=(old_content is None or bool(owned.get(str(path), {}).get("file_created", False) and
                                                 action["observed"]["sha256"] == owned[str(path)]["sha256"])),
                                        prefix_added=prefix_added))
                owned[str(path)] = entry
                if fail_after is not None and len(created) + len(replaced) >= fail_after:
                    raise RuntimeError("injected install interruption")
            receipt = {key: plan[key] for key in ("schema_version", "install_id", "profile", "project",
                                                  "scope", "user_home", "codex_home", "generation",
                                                  "runtime_version", "data_root")}
            clean_owned = [{key: row[key] for key in ("path", "sha256", "file_id", "kind",
                           "block_sha256", "block_profile", "file_created", "prefix_added") if key in row} for row in owned.values()]
            receipt.update(owned=sorted(clean_owned, key=lambda row: row["path"]),
                           manual_pending=[item["path"] for item in plan["manual_patches"]])
            if receipt != previous:
                atomic_json(receipt_path, receipt)
            for row in [*planned["recovery"].values(), *planned["prepared"].values(), *journal_owned.values()]:
                staging = row.get("staging_path")
                if staging and Path(staging).is_file() and _unchanged({**row, "path": staging}):
                    Path(staging).unlink()
                backup = row.get("backup_path")
                if backup and Path(backup).is_file() and _observed(Path(backup))["sha256"] == row.get("prior_sha256"):
                    Path(backup).unlink()
            journal_path.unlink(missing_ok=True)
        except Exception:
            remaining = list(planned["recovery"].values())
            for entry, backup in reversed(replaced):
                try:
                    target = Path(entry["path"])
                    if (target.exists() and _observed(target)["sha256"] == entry["sha256"] and
                            [target.stat().st_dev, target.stat().st_ino] == entry["file_id"]):
                        if backup and Path(backup).is_file() and _observed(Path(backup))["sha256"] == entry["prior_sha256"]:
                            os.replace(backup, target)
                        else:
                            remaining.append(entry)
                    elif target.exists() and _observed(target)["sha256"] == entry["prior_sha256"]:
                        if backup:
                            Path(backup).unlink(missing_ok=True)
                    else:
                        remaining.append(entry)
                except (OSError, ValueError, ConflictError):
                    remaining.append(entry)
            for entry in reversed(created):
                try:
                    if not Path(entry["path"]).exists():
                        continue
                    if _whole_unchanged(entry):
                        Path(entry["path"]).unlink()
                    else:
                        remaining.append(entry)
                except (OSError, ValueError):
                    remaining.append(entry)
            if remaining:
                journal["owned"] = remaining
                atomic_json(journal_path, journal)
            else:
                journal_path.unlink(missing_ok=True)
            raise
    return {"status": plan["integration_status"], "receipt": str(receipt_path),
            "owned_count": len(receipt["owned"]), "manual_pending": receipt["manual_pending"],
            "unresolved_requirements": plan["unresolved_requirements"]}


def setup(profile, project=None, *, scope=None, application_roots=None, generation=None,
          budget_model=None, user_home=None, codex_home=None):
    """Check collisions and install through the same reviewed transaction path."""
    descriptor = read_json(load_registry()["profiles"][profile]["descriptor"])
    scope = scope or ("user" if "user" in descriptor.get("installation_scopes", {}) else "project")
    options = {"scope": scope, "automatic": True, "application_roots": application_roots,
               "generation": generation, "budget_model": budget_model,
               "user_home": user_home, "codex_home": codex_home}
    plan = plan_install(profile, project, **options)["plan"]
    if plan["status"] == "blocked":
        issues = [row["path"] for row in plan["actions"] if row["action"].startswith("conflict")]
        issues.extend(row["path"] for row in plan["skill_conflicts"])
        raise ConflictError("setup blocked by existing content: " + ", ".join(issues))
    result = apply_install(profile, project, plan["plan_digest"], **options)
    persistent = [row for row in plan["actions"] if row["role"] == "persistent"]
    budget = persistent[0]["budget"] if persistent else None
    instruction_budgets = [
        {"path": row["path"], "managed_entry_bytes": row["budget"].get("managed_entry_bytes"),
         "complete_file_bytes": row["budget"].get("bytes"), "status": row["budget"].get("status"),
         "tokens": row["budget"].get("tokens"), "counter": row["budget"].get("counter")}
        for row in persistent]
    warnings = list(plan["warnings"])
    if budget_model is not None and budget and budget.get("under_1000") is not True:
        warnings.append("Persistent instruction token budget is " +
                        ("over the selected model limit" if budget.get("under_1000") is False
                         else "unverified without an installed tokenizer/model binding"))
    return {"status": result["status"], "profile": profile, "scope": scope,
            "receipt": result["receipt"], "installed_count": result["owned_count"],
            "commands": plan["commands"], "instruction_paths": [row["path"] for row in plan["actions"]
                                                  if row["role"] == "persistent"],
            "instruction_budgets": instruction_budgets, "warnings": warnings,
            "unresolved_requirements": result["unresolved_requirements"]}


def plan_uninstall(receipt_file, *, application_roots=None):
    original = Path(receipt_file).expanduser().absolute()
    _no_links(original)
    data = Path((application_roots or roots())["data"]).expanduser().resolve()
    path = original
    receipt = _record(path)
    if receipt is None:
        if path.parent != data / "receipts":
            raise ContractError("receipt is outside application data root")
        public = {"schema_version": 1, "receipt": str(path), "status": "absent", "actions": []}
    else:
        _validate_record(path, receipt, data)
        actions = []
        for entry in receipt["owned"]:
            target = Path(entry["path"])
            observed = _observed(target)
            if entry.get("kind") == "block" and observed["exists"]:
                try:
                    block = _managed_block(target.read_bytes().decode("utf-8"), receipt["profile"])
                except (UnicodeError, ContractError):
                    block = "invalid"
                action = ("already_absent" if block is None else
                          "remove_block" if _unchanged(entry) else "preserve_edited")
            else:
                action = ("already_absent" if not observed["exists"] else
                          "remove_owned" if _unchanged(entry) else "preserve_edited")
            actions.append({"path": str(target), "action": action, "owned_sha256": entry["sha256"], "observed": observed})
        public = {"schema_version": 1, "receipt": str(path), "receipt_input": _observed(path),
                  "status": "conflict" if any(a["action"] == "preserve_edited" for a in actions) else "ready",
                  "actions": actions}
    public["plan_digest"] = _hash(canonical(public))
    return public


def apply_uninstall(receipt_file, plan_digest, *, application_roots=None):
    initial = plan_uninstall(receipt_file, application_roots=application_roots)
    if initial["plan_digest"] != plan_digest:
        raise ConflictError("removal plan changed; rerun dry run")
    if initial["status"] == "absent":
        return {"status": "absent", "removed": 0, "preserved": []}
    receipt_path = Path(initial["receipt"])
    with locked(receipt_path.with_suffix(".lock")):
        plan = plan_uninstall(receipt_file, application_roots=application_roots)
        if plan["plan_digest"] != plan_digest:
            raise ConflictError("removal inputs changed before lock")
        receipt = _record(receipt_path)
        # Validate every input before the first deletion.
        for action in plan["actions"]:
            if _observed(Path(action["path"])) != action["observed"]:
                raise ConflictError("removal input changed")
        removed, preserved = 0, []
        for action in plan["actions"]:
            target = Path(action["path"])
            if _observed(target) != action["observed"]:
                raise ConflictError("removal input changed: " + str(target))
            if action["action"] == "remove_owned":
                target.unlink()
                removed += 1
            elif action["action"] == "remove_block":
                original = target.read_bytes().decode("utf-8")
                block = _managed_block(original, receipt["profile"])
                if block is None:
                    raise ConflictError("managed block changed during removal")
                entry = next(row for row in receipt["owned"] if row["path"] == str(target))
                if entry.get("file_created") and action["observed"]["sha256"] == entry["sha256"]:
                    target.unlink()
                    removed += 1
                    continue
                before = original[:block[0]]
                prefix = entry.get("prefix_added", "")
                if prefix and before.endswith(prefix):
                    before = before[:-len(prefix)]
                remaining = before + original[block[1]:]
                if not remaining and entry.get("file_created"):
                    target.unlink()
                else:
                    _replace_existing(target, remaining.encode("utf-8"), action["observed"]["sha256"])
                removed += 1
            elif action["action"] == "preserve_edited":
                preserved.append(str(target))
        if preserved:
            receipt["owned"] = [entry for entry in receipt["owned"] if entry["path"] in preserved]
            atomic_json(receipt_path, receipt)
        else:
            receipt_path.unlink()
    return {"status": "partial" if preserved else "removed", "removed": removed, "preserved": preserved}
