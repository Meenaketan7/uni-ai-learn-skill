# Runtime contracts

The shared wire formats are the six JSON schema files in `core/schemas/`.
All runtime records use integer `schema_version: 1`. The implementation
validates the schema features it consumes with the standard library; it does
not claim to implement a general JSON Schema engine.

## Identity, ownership and errors

Project identity is a digest of the resolved absolute root, including separate
worktrees. Identically named directories do not share state. Moving a project never
silently reassigns its history. IDs contain 1 to 128 ASCII letters, digits, hyphens
or underscores. Generated task/event/checkpoint IDs use UUIDs.

Unknown top level state, handoff, checkpoint, log and model fields are rejected.
Source references and adapter capabilities have explicitly documented extension
surfaces. A bad record raises `ContractError` (a `ValueError`); changed
revisions, busy locks and stale plans raise `ConflictError`. A capped log raises
`LogFullError`. CLI failures produce a JSON diagnostic on stderr and exit 2.
Read operations do not create absent state.

| Record | Owner and required content |
|---|---|
| Project | Shared preferences: project ID, revision, learning/logging flags and byte cap. |
| Task | One project/session/task: objective, lifecycle, bypass, explicit activation, attempts, predictions and pending status, hypotheses, misconceptions, hints, internal intent, workflow, acceptance, decisions, failures, questions, next action, difficulty evidence and checkpoint anchor. |
| Handoff | Task ID/class, objective, constraints, sourced findings, dated/revisioned sources, uncertainty, failed attempts and next decision. |
| Checkpoint | Identity/revision, task revision and hash, project revision, mode/bypass, active work, decisions/reasons/sources, failed approaches, open questions, applicable learning fields, reconstructable pointers and retention audit. |
| Log | Stable event/concept IDs, UTC time, project/task, tags, prompt, attempt, misconception/correction, outcome, due date, review link and success streak. |
| Model registry | Task tiers, acyclic fallback graph, qualified records, host bindings, context/tool limits, effort maps, nullable prices/provenance, overrides and current model fallback. |
| Adapter result | Version, profile, unique relative artifacts, exactly seven native command mappings, capabilities, warnings and manual patch proposals. |

## Filesystem and state transactions

Project preferences and each task use separate bounded exclusive locks.
A lock is never removed because of age, and cleanup checks the lock's inode.
Writes use a temporary file in the same directory, fsync, and atomic replacement.
Updates compare a caller's expected revision. CLI read/append/update paths carry
the revision they read, preventing a concurrent writer's changes from being
silently replaced. A different objective requires a new task; closing ends
the task's shipping bypass.

Task state and checkpoints are required local continuity data, independent of
the optional history log. Log append locks preferences and then the log, validates
the existing file, and counts UTF-8 bytes plus newline before writing. It never
truncates, rotates or repairs history. Duplicate events and stale concept reviews
are rejected. Recall builds a disposable SQLite index from a local stream, stores
offsets/lengths/hashes, chooses the latest event per concept, and returns at most
five records. Missing/capped logging permits session/topic recall.

## Learning dispatch

`learning.dispatch(project, session, task, command, argument, ...)` returns an
action, task state and only relevant resource paths. Mode changes and asking for
an objective can run without a task. Explicit teaching selects a task move while
the shared project preference can remain off. Compact does not activate learning
and remains available during shipping.

`learning.transition(task, event)` validates deterministic progress events:
attempt, prediction, hypothesis, hint, request_answer, reveal, observe,
misconception, decision, failure, open_question, intent, teach_back, ship, accept.
A pending prediction authorizes one revealing action. Hint levels advance one
step to a maximum of three. Full answers require an adequate recorded attempt
and explicit request, or bypass. Semantic adequacy and teaching quality remain
agent judgments requiring transcripts; these gates do not prove them.

## Adapter boundary

`uni_learn.adapters.render(profile, context, registry=None)` finds a registered
renderer and validates its output independently of the renderer. Its callable is:

```python
render(context: dict, read_resource) -> dict
```

`read_resource(path)` returns UTF-8 resource bytes. Context supplies profile,
project root, durable runtime/resource paths, version, runtime command and any
explicit generation choice. Results use a command mapping keyed by canonical ID,
not a separate shared runtime branch for each host:

For project scope, `project_root` is required and absolute. Rendering resolves it
and supplies a correctly quoted `${project_arg}` alongside `${runtime_command}`.
Native dispatchers use that binding for every state/task/learning command, so a
cwd change cannot select another project. A normal session with learning enabled
lazily opens `core/learning/session.md`; off mode does not load that teaching resource.

```json
{
  "schema_version": 1,
  "profile": "example",
  "capabilities": {},
  "warnings": [],
  "manual_patches": [],
  "commands": {
    "learn": {"invocation": "/example:learn", "path": ".example/learn.md"}
  },
  "artifacts": [
    {"path": ".example/learn.md", "role": "command", "command": "learn", "content": "Native dispatcher"}
  ]
}
```

This fragment illustrates the shape; a valid result must contain all seven
canonical IDs. Artifacts use `command`, `metadata` or `persistent` roles.
Output paths, command paths and native spellings must be unique, contained and
consistent. Descriptors provide concrete `files` and `commands` entries;
`render_spec` substitutes `string.Template` variables in their native templates.
A new adapter needs only registered resources. Tests prove this with a fixture
adapter that uses only memory and does not change files in the shared core.

## Installation and measurement

Dry runs build deterministic plans in memory and write nothing. Plans include
profile/generation, version/package hash, target hashes/absence, ownership inputs,
command mappings, complete manual entry proposals, diffs, conflicts and a
SHA-256 digest of canonical JSON. Apply recomputes that digest under the receipt
lock and checks targets again.

A staged inode is journaled before its destination is committed using an
exclusive hard link. Interrupted transactions can recognize their own files
without adopting an identical file created by someone else. Exceptions roll back
only unchanged proven files. Receipts are contained in the selected application
data root and may name only registered command, metadata, or persistent artifacts
and their own release payload. Symlink ancestors and escaping paths are rejected.
Each host receipt has its own runtime and resource payload. Removal preserves
drifted files, unrelated configuration and learning history.

Automatic `setup` merges a persistent block delimited by markers and records
ownership of that block. Unknown token counts are reported without blocking
installation.
An optional exact counter can reject a known excessive complete entry before
writes. The legacy `install` preview/apply API retains manual proposals when
called without automatic mode.

`--budget-model MODEL` reuses an installed tiktoken mapping only on explicit
request. Vocabulary assets must already be cached and match the library's
expected SHA-256. Missing or corrupt assets produce a diagnostic without
fetching, deleting, or rewriting them. Counter provenance, model, count, and
content hash participate in the plan. A count of a managed block never certifies
surrounding user text or expanded imports, and no count proves native loading.

## Compaction integrity

Prepare retains task facts by default and merges supplied additions without
removing constraints. The merged inventory also updates task state so later
checkpoints preserve those additions. It saves/reopens a checkpoint and anchors its complete
content digest and revision in task state under the task lock. The saved task
digest excludes only this anchor to avoid a circular hash. Resume verifies that
anchor, task revision/hash, project revision, mandatory fields, audit and pointers.

Active learning retains predictions, attempts, hypotheses, corrections, hint
level, intent and difficulty evidence even when logging is disabled. Checkpoints
created while learning is off have no invented learning object. Unique
conversation facts must be inline or durably recorded. A relocated symbol
remains stale evidence until its changed conclusion is reviewed. References from
the web or integrations require a current local fallback for unattended resume.
Known missing context blocks resume.

Preparation reports `native_compaction_executed: false`. The adapter's native
procedure or a fresh session is separate; resume never claims that a file write
cleared live context.

## Routing and orchestration

The initial real model catalogue is empty. Registry records cannot authorize
execution without task/profile qualification, current availability, tools, capacity
including handoff, and effort support. Availability observations older than one
hour are not fresh. Qualification ordering compares UTC instants; an equally
dated failure cannot be hidden by a pass.

Precedence is explicit task override, project override, global override, default.
An explicit model pin supplies its tier unless the request also restricts tier.
Costs need known comparable units/provenance; unknown prices never become zero.
Explicit ranks or eligible pins can resolve an otherwise unpriced choice.
Handoff overhead can make retaining the current model the economical decision.

The policy returns recommendations. Requested and observed execution fields stay
empty until host execution evidence exists outside the selector. Orchestration
requires an approved plan before implementation and actual diff/check evidence
before review; it accepts a validated sourced handoff. Unsupported controls keep
a visible single model flow. No model account or paid benchmark is launched.

## Native skill collision inventory

Planning inventories native SKILL.md files in the selected scope. Paths, hashes,
decoded names, and ambiguous name status participate in the plan. A canonical
command name in another folder blocks installation; unsupported or ambiguous
YAML names require review. Unrelated linked skills may be inspected without
being adopted. Installation targets and ancestors must not be symlinks.
Existing owned files retain their drift and reinstall checks.

## Automatic setup and user scope

`setup(profile, project=None, scope=None, ...)` chooses declared user scope
when available, otherwise project scope. It plans, detects collisions, validates
targets again under the installation lock, and applies through the existing
transaction path. User approval is the invocation of setup; no externally
copied digest or manual instruction patch is required.

Codex declares user skills below HOME/.agents/skills and instructions under
CODEX_HOME. If AGENTS.override.md contains any character other than whitespace, it takes
precedence. An override containing only whitespace is ignored, so AGENTS.md
remains effective. Setup leaves a whitespace only override unchanged.
Project identity is resolved once per session through `project locate`,
using the nearest Git/worktree marker or initial plain directory.

Automatic writes to persistent files own only the block marked by delimiters;
surrounding instructions remain intact. UTF-8 bytes, CRLF, blank lines and native
frontmatter outside the block survive setup, reinstall and removal. Edited owned
blocks are preserved. Ownership applies to each generated file as a whole. The
installer can update a file only while its recorded content remains intact.

A replacement stages and fsyncs new content, records its intent and original
inode backup before committing, and rechecks the current target. Rollback
restores original inodes. Crash recovery first recognizes commits proven by the
journal, even when receipt hashes are stale. Concurrent user edits prevent
deletion of the entire file. Managed block receipts do not certify surrounding
user imports. Optional exact counters report their scope; a known excessive
complete entry blocks automatic application. Unknown counts require no manual
action and are reported in JSON.

Runtime requirements remain Python 3.11+ and its standard library. There is no
daemon or hosted dependency. Release checks contact the network only when
requested or when optional update notices are enabled.

## Distribution

The standalone runtime is a deterministic ZIP application assembled from an
explicit resource allowlist. Distribution ZIPs add the installer, license, and
installation instructions. Build metadata binds their hashes to a full Git
commit and runtime version.

The remote installer selects one published managed release before downloading
its metadata and runtime. It verifies content before invoking setup with an argv
list. The latest and previous channels refer to the newest two successful
managed builds, not untested branch contents. Publication uploads a complete
draft before promoting it, then removes only older releases recognized by the
publisher's exact tag pattern and body marker. Unrelated releases are preserved.
