"""Local CLI for state, learning, routing, compaction, and installation."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from . import __version__
from . import compaction, install, integrations, learning, learning_log, package, recall, routing, state, updates
from .contracts import ContractError
from .paths import locate_project


def parser():
    p = argparse.ArgumentParser(prog="uni-learn")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="area", required=True)

    project = sub.add_parser("project", help="resolve the current project once per session")
    projects = project.add_subparsers(dest="operation", required=True)
    locate = projects.add_parser("locate")
    locate.add_argument("--start", default=".")
    locate.add_argument("--json", action="store_true")

    st = sub.add_parser("state", help="project preferences")
    s = st.add_subparsers(dest="operation", required=True)
    show = s.add_parser("show")
    show.add_argument("--project", required=True)
    show.add_argument("--json", action="store_true")
    set_ = s.add_parser("set")
    set_.add_argument("--project", required=True)
    set_.add_argument("--learning", choices=("on", "off"))
    set_.add_argument("--logging", choices=("on", "off"))
    set_.add_argument("--log-cap-bytes", type=int)
    set_.add_argument("--expected-revision", type=int)
    set_.add_argument("--json", action="store_true")

    t = sub.add_parser("task", help="task lifecycle")
    ts = t.add_subparsers(dest="operation", required=True)
    for name in ("start", "show", "update", "ship", "close"):
        q = ts.add_parser(name)
        q.add_argument("--project", required=True)
        q.add_argument("--session", required=True)
        if name == "start":
            q.add_argument("--objective", required=True)
            q.add_argument("--task")
        else:
            q.add_argument("--task", required=True)
        if name == "update":
            for key in ("attempt", "prediction", "hypothesis", "misconception", "constraint", "criterion"):
                q.add_argument("--" + key, action="append")
            q.add_argument("--intent")
            q.add_argument("--next-action")
            q.add_argument("--expected-revision", type=int)
            q.add_argument("--event-file", metavar="JSON_FILE", help="path to a JSON event file")
            q.add_argument("--changes", metavar="JSON_FILE", help="path to a JSON object of task changes (not inline JSON)")
        q.add_argument("--json", action="store_true")

    l = sub.add_parser("learn", help="dispatch explicit teaching move")
    l.add_argument("--project", required=True)
    l.add_argument("--session")
    l.add_argument("--task")
    l.add_argument("--command", choices=tuple(learning.INTENTS), required=True)
    l.add_argument("--argument", default="")
    l.add_argument("--domain", action="append", choices=tuple(sorted(learning.DOMAINS)))
    l.add_argument("--full-answer", action="store_true", help="flag: user explicitly requested a full answer; takes no value")
    l.add_argument("--revealing-action", action="store_true", help="flag: consume a recorded prediction before revealing evidence; takes no value")
    l.add_argument("--json", action="store_true")

    d = sub.add_parser("doctor", help="read-only local inventory")
    d.add_argument("--project", default=".")
    d.add_argument("--json", action="store_true")

    r = sub.add_parser("route", help="recommend a model from qualified bindings")
    r.add_argument("--request", required=True)
    r.add_argument("--json", action="store_true")

    c = sub.add_parser("compact", help="checkpoint around host compaction")
    cs = c.add_subparsers(dest="operation", required=True)
    prep = cs.add_parser("prepare")
    prep.add_argument("--request", required=True)
    prep.add_argument("--json", action="store_true")
    res = cs.add_parser("resume")
    res.add_argument("--checkpoint", required=True)
    res.add_argument("--project", required=True)
    res.add_argument("--json", action="store_true")

    rec = sub.add_parser("recall", help="retrieve bounded due learning entries")
    rec.add_argument("--project", required=True)
    rec.add_argument("--limit", type=int, default=5)
    rec.add_argument("--topic")
    rec.add_argument("--session")
    rec.add_argument("--task")
    rec.add_argument("--json", action="store_true")

    log = sub.add_parser("log", help="append optional learning events or retrieval outcomes")
    logs = log.add_subparsers(dest="operation", required=True)
    for operation in ("append", "review"):
        item = logs.add_parser(operation)
        item.add_argument("--project", required=True)
        item.add_argument("--json", action="store_true")
        if operation == "append":
            item.add_argument("--request", required=True)
        else:
            item.add_argument("--entry", required=True)
            item.add_argument("--attempt", required=True)
            item.add_argument("--outcome", required=True, choices=("pass", "fail"))

    resource = sub.add_parser("resource", help="read a packaged shared resource")
    resource.add_argument("--path", required=True)
    resource.add_argument("--json", action="store_true")

    i = sub.add_parser("install", help="dry run or apply host artifacts")
    i.add_argument("--host", required=True)
    i.add_argument("--project")
    i.add_argument("--scope", choices=("project", "user"), default="project")
    i.add_argument("--generation")
    i.add_argument("--budget-model", help="Reuse installed tiktoken for this exact host model; never installs dependencies")
    ig = i.add_mutually_exclusive_group()
    ig.add_argument("--dry-run", action="store_true")
    ig.add_argument("--apply", action="store_true")
    i.add_argument("--plan-digest")
    i.add_argument("--json", action="store_true")

    setup = sub.add_parser("setup", help="check conflicts and install automatically in one command")
    setup.add_argument("--host", required=True)
    setup.add_argument("--scope", choices=("project", "user"))
    setup.add_argument("--project")
    setup.add_argument("--generation")
    setup.add_argument("--budget-model")
    setup.add_argument("--json", action="store_true")

    update = sub.add_parser("update", help="download and install a verified release for an existing installation")
    update.add_argument("--receipt", help="installation receipt; required when more than one installation matches")
    update.add_argument("--channel", choices=("latest", "previous"), default="latest")
    update.add_argument("--repository", default=updates.REPOSITORY)
    update.add_argument("--json", action="store_true")

    notices = sub.add_parser("updates", help="check releases and manage optional update notices")
    operations = notices.add_subparsers(dest="operation", required=True)
    for operation in ("check", "enable", "disable", "status"):
        item = operations.add_parser(operation)
        if operation in {"check", "enable"}:
            item.add_argument("--receipt")
            item.add_argument("--repository", default=updates.REPOSITORY)
        item.add_argument("--json", action="store_true")

    u = sub.add_parser("uninstall", help="preview or reverse owned artifacts")
    u.add_argument("--receipt", required=True)
    ug = u.add_mutually_exclusive_group()
    ug.add_argument("--dry-run", action="store_true")
    ug.add_argument("--apply", action="store_true")
    u.add_argument("--plan-digest")
    u.add_argument("--json", action="store_true")

    pk = sub.add_parser("package", help="build reproducible zipapp")
    pk.add_argument("--output", required=True)
    pk.add_argument("--json", action="store_true")
    return p


def execute(args):
    if args.area == "project":
        return locate_project(args.start)
    if args.area == "state":
        if args.operation == "show":
            result = state.show_project(args.project)
            notice = updates.notification(project=args.project)
            if notice:
                result["update_notice"] = notice
            return result
        if args.learning is None and args.logging is None and args.log_cap_bytes is None:
            raise ValueError("state set needs an explicit setting")
        return state.set_project(args.project, learning=None if args.learning is None else args.learning == "on",
                                 logging=None if args.logging is None else args.logging == "on",
                                 log_cap_bytes=args.log_cap_bytes, expected_revision=args.expected_revision)
    if args.area == "task":
        if args.operation == "start":
            return state.new_task(args.project, args.session, args.task or uuid.uuid4().hex, args.objective)
        if args.operation == "show":
            return state.show_task(args.project, args.session, args.task)
        if args.operation == "ship":
            return state.ship_task(args.project, args.session, args.task)
        if args.operation == "close":
            return state.close_task(args.project, args.session, args.task)
        current = state.show_task(args.project, args.session, args.task)
        changes = {}
        if args.event_file:
            changed = learning.transition(current, json.loads(Path(args.event_file).read_text("utf-8")))
            changes.update({key: value for key, value in changed.items() if value != current[key]})
        if args.changes:
            supplied = json.loads(Path(args.changes).read_text("utf-8"))
            if not isinstance(supplied, dict):
                raise ContractError("task changes must be an object")
            changes.update(supplied)
        for argument, field in (("attempt", "attempts"), ("prediction", "predictions"), ("hypothesis", "hypotheses"),
                                ("misconception", "misconceptions"), ("constraint", "constraints"), ("criterion", "acceptance_criteria")):
            entries = getattr(args, argument)
            if entries:
                changes[field] = current[field] + entries
        if args.intent is not None:
            changes["intent"] = args.intent
        if args.next_action is not None:
            changes["next_action"] = args.next_action
        if not changes:
            raise ValueError("task update needs a change")
        return state.update_task(args.project, args.session, args.task, changes,
                                 expected_revision=current["revision"] if args.expected_revision is None else args.expected_revision)
    if args.area == "learn":
        return learning.dispatch(args.project, args.session, args.task, args.command, args.argument, domains=args.domain,
                                 full_answer=args.full_answer, revealing_action=args.revealing_action)
    if args.area == "doctor":
        from .adapters import load_registry
        from shutil import which
        profiles = {}
        for key, entry in load_registry()["profiles"].items():
            spec = package.read_json(entry["descriptor"])
            profiles[key] = {"status": "found" if which(spec["executable"]) else "missing",
                             "executable": which(spec["executable"]), "live_behavior": "unverified"}
        return {"project": str(Path(args.project).resolve()),
                "scope": "PATH executables and recognized project JSON configurations; editor extensions and app connectors unverified",
                "integrations": integrations.detect(project=args.project),
                "hosts": profiles, "network_health": "unverified"}
    if args.area == "route":
        return routing.choose(json.loads(Path(args.request).read_text("utf-8")))
    if args.area == "compact":
        if args.operation == "prepare":
            return compaction.prepare(json.loads(Path(args.request).read_text("utf-8")))
        return compaction.resume(args.project, args.checkpoint)
    if args.area == "recall":
        return recall.recall(args.project, limit=args.limit, topic=args.topic, session=args.session, task=args.task)
    if args.area == "log":
        if args.operation == "append":
            supplied = json.loads(Path(args.request).read_text("utf-8"))
            event = learning_log.validate_event(supplied) if "schema_version" in supplied else learning_log.make_event(args.project, **supplied)
        else:
            original = next((record for _, _, record in learning_log.scan(learning_log.log_path(args.project))
                             if record["entry_id"] == args.entry), None)
            if original is None:
                raise ContractError("learning entry missing")
            event = recall.review_event(args.project, original, success=args.outcome == "pass", attempt=args.attempt)
        return learning_log.append(args.project, event)
    if args.area == "resource":
        return {"path": args.path, "content": package.read_resource(args.path).decode("utf-8")}
    if args.area == "install":
        if args.apply:
            if not args.plan_digest:
                raise ValueError("apply requires --plan-digest")
            return install.apply_install(args.host, args.project, args.plan_digest, generation=args.generation,
                                         budget_model=args.budget_model, scope=args.scope)
        return install.plan_install(args.host, args.project, generation=args.generation,
                                    budget_model=args.budget_model, scope=args.scope)["plan"]
    if args.area == "setup":
        return install.setup(args.host, args.project, scope=args.scope, generation=args.generation,
                             budget_model=args.budget_model)
    if args.area == "update":
        return updates.update(receipt=args.receipt, channel=args.channel, repository=args.repository)
    if args.area == "updates":
        if args.operation == "check":
            return updates.check_available(receipt=args.receipt, repository=args.repository)
        if args.operation == "status":
            return updates.status()
        if args.operation == "enable":
            return updates.configure(True, receipt=args.receipt, repository=args.repository)
        return updates.configure(False)
    if args.area == "uninstall":
        if args.apply:
            if not args.plan_digest:
                raise ValueError("apply requires --plan-digest")
            return install.apply_uninstall(args.receipt, args.plan_digest)
        return install.plan_uninstall(args.receipt)
    if args.area == "package":
        return package.build(Path(args.output))
    raise ValueError("unknown command")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2
    if args.area == "setup" and not args.json:
        invocation = result["commands"]["learn"]["invocation"]
        usage = (f"In chat, load {invocation}; then send: learn on"
                 if invocation.startswith("/read ") else f"In chat: {invocation} on")
        summary = "\n".join((
            f"Uni Learn {__version__}: {result['status']} ({result['profile']}, {result['scope']})",
            f"Learning moves: {len(result['commands'])}; installed once for this scope.",
            "Instructions: " + ", ".join(result["instruction_paths"]),
            "Receipt: " + result["receipt"],
            "Start a fresh assistant session. Learning starts off.",
            usage,
        ))
        encoding = sys.stdout.encoding or "utf-8"
        print(summary.encode(encoding, errors="backslashreplace").decode(encoding))
    elif args.area in {"update", "updates"} and not args.json:
        status = result["status"]
        if status == "updated":
            summary = f"Updated Uni Learn to {result['version']}. Start a fresh assistant session."
        elif status == "updated_partial":
            summary = (f"Installed Uni Learn {result['version']}; some integration requirements remain unresolved.\n"
                       "Receipt: " + result["receipt"] + "\n"
                       "Details: " + json.dumps(result["setup"], sort_keys=True))
        elif status == "available":
            summary = (f"Uni Learn {result['version']} is available; installed: {result['current_version']}.\n"
                       "Use the update command with the same runtime to install it.")
        elif status == "current":
            summary = f"Uni Learn {result.get('current_version', result.get('version'))} is current."
        elif status == "unavailable":
            summary = "Update check unavailable: " + result["message"]
        else:
            summary = "Update notices " + status + "."
        encoding = sys.stdout.encoding or "utf-8"
        print(summary.encode(encoding, errors="backslashreplace").decode(encoding))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
