# Installation and use

Start with the [README](../README.md) for remote installation and chat commands.
Python 3.11+ and your coding assistant are required. No external runtime
packages are needed. The commands below use a checkout; replace
`python3 -m uni_learn` with `python3 /path/to/uni-learn.pyz` for a downloaded build.
On Windows, `py -3` can replace `python3`.

## Setup and updates

```sh
python3 -m uni_learn setup --host codex
python3 -m uni_learn setup --host cursor --project /absolute/path/to/project
```

Codex defaults to user scope. Skills go below `~/.agents/skills` and the managed
instruction block goes into the effective instruction file under CODEX_HOME
(default `~/.codex`). If AGENTS.override.md contains any nonwhitespace text, it
takes precedence. A whitespace only override does not, so AGENTS.md remains
effective. Setup preserves existing text, CRLF, native frontmatter, and unrelated
skills.

Other profiles use project scope. Supply `--scope project` to install Codex in
one project instead. The Windsurf profile targets Devin Desktop and requires
`--generation devin-desktop`. Its legacy generation is not assumed.

Setup checks collisions and file ownership before changing files. Rerunning the
same command reuses intact files or upgrades them. User edits to an owned skill
or managed block stop replacement and identify the affected path. Review that
specific edit before retrying. No shared TOML, JSONC, MCP configuration, model
account, or optional tool is installed automatically.

A successful setup prints `ready`, the receipt path, restart instructions, and
the selected host's exact chat entry point.
Use `--json` for paths, ownership, warnings, capabilities, and instruction bytes.
An exact token count is optional; lack of a tokenizer never requires a manual
instruction edit in the automatic setup flow.

For the remote installer, use the same arguments plus `--channel previous` to
select the previous successful build. It verifies the runtime against the
selected release's metadata before running setup. The previous channel becomes
available after two successful builds have been published. A private repository
requires authenticated asset access; the public single command examples assume a
public repository.

### Updates

The runtime does not update itself. With notices disabled, it does not contact
GitHub during ordinary work. Check for a release when you choose:

```sh
python3 -m uni_learn updates check
```

To install the selected release, use the receipt from setup:

```sh
python3 -m uni_learn update --receipt /path/to/receipt.json
```

The update command verifies the release before setup and preserves project state.
It follows the same conflict and edited file checks as a new install. To select
the previous successful build, add `--channel previous`.

Update notices are optional and disabled by default. When enabled, the runtime
checks no more than once per day and caches the result locally:

```sh
python3 -m uni_learn updates enable
python3 -m uni_learn updates status
python3 -m uni_learn updates disable
```

When notices are enabled, `state show` includes an available update notice from
the daily cached check. A failed network check is cached for the same interval
and produces no update notice. The assistant can mention a newer release and its
version; installing it remains an explicit `update` command. Run `updates check`
to request another check immediately.

## Projects and learning mode

User wide Codex skills resolve the nearest Git/worktree root when a session
starts, or the initial directory for a plain folder. That absolute root is kept
through later directory changes. Preferences are shared by sessions in that
project; task progress belongs to a session and objective. Separate worktrees
have separate identities. Moving a project changes its identity.

```sh
python3 -m uni_learn project locate --start . --json
python3 -m uni_learn state show --project . --json
python3 -m uni_learn learn --project . --command learn --argument on --json
python3 -m uni_learn learn --project . --command learn --argument off --json
```

Learning and history logging both start off. In chat, use `$learn on` or
`$learn off` for Codex and the corresponding native invocation in other
assistants. Turning off stops guidance immediately; a fresh session clears
teaching instructions already loaded into the conversation. When turning off a
runtime task, include its session and task IDs to clear its explicit activation.

### Commands across assistants

Use the [platform reference](PLATFORMS.md) to choose the native invocation for
all twelve profiles. Most expose `/learn`; documentation for Codex CLI and the
IDE uses `$learn` and `/skills`. The desktop app lists enabled skills in `/`.
Kimi supports `/skill:learn`, Antigravity uses `/uni-learn` to avoid its unrelated
built in `/learn`, and Aider loads an instruction file with `/read`.

These entry points activate a skill, command prompt, or file dispatcher. The
assistant then calls the Uni Learn runtime to persist `on` or `off`. They do not
register a new built in Plan/Ask/Act mode or change the host's permissions. If the
host blocks runtime execution in a restricted mode, use its permitted agent
workflow or approve the requested command. Where the host may invoke a skill
automatically, its dispatcher checks the saved learning preference and stops
when learning is off, unless the user explicitly invokes a Uni Learn command.

An explicit learning move activates a task without switching the project on.
An adequate existing attempt or prediction counts: the assistant should not ask
you to repeat it. Hints advance one step at a time, up to level three. A full
answer requires an attempt and explicit request. Saying `ship this` requests a
teaching bypass for that objective and its corrections; a new objective uses
the project preference again. Requesting a full answer does not itself request
shipping.

The domain packs are `coding`, `math-proof`, `product-system`, and
`research-projects`. The assistant chooses the relevant pack rather than loading
all four. It supplies the explanation; the runtime enforces task and learning
state transitions.

## Runtime task example

Most users can stay in chat. These commands are useful when checking the runtime:

```sh
python3 -m uni_learn task start --project . --session demo --task balance --objective "Explain the transfer invariant" --json
python3 -m uni_learn task update --project . --session demo --task balance --attempt "The total should stay constant" --prediction "A rejected transfer leaves the entire dict unchanged" --json
python3 -m uni_learn learn --project . --session demo --task balance --command learn-hint --domain coding --json
python3 -m uni_learn task show --project . --session demo --task balance --json
```

Use one task through corrections; start a new task for a new objective. The
assistant records predictions before revealing actions. `--revealing-action`
consumes one pending prediction. `--full-answer` handles an explicit answer
request. Follow the returned action; selecting resources does not require a
second dispatch. Use `task ship` only for a requested teaching bypass, and
`task close` when the objective is accepted as complete.

## Optional history and recall

```sh
python3 -m uni_learn state set --project . --logging on --json
python3 -m uni_learn log append --project . --request learning-event.json --json
python3 -m uni_learn recall --project . --limit 5 --json
```

An event file can contain:

```json
{
  "task_id": "balance",
  "tags": ["invariant", "coding"],
  "prompt": "What changes after a rejected transfer?",
  "attempt": "I predicted both balances remain unchanged.",
  "correction": "Validate every rejection condition before mutation.",
  "outcome": "pass"
}
```

The runtime supplies IDs, time, and review metadata. Record a later retrieval
using the returned entry ID:

```sh
python3 -m uni_learn log review --project . --entry ENTRY_ID --attempt "Preserve the whole dict on failure" --outcome pass --json
```

History is append only UTF-8 JSONL with a default 1 MiB cap. At the cap, logging
pauses without rewriting past entries. Successful reviews schedule 1, 3, 7, 14,
then 30 days; the initial entry is due in one day and its first successful review
advances to three. Recall returns at most five due concepts through a rebuildable
SQLite index. With logging off, session or selected topic recall remains
available. Task state and checkpoints do not depend on optional history.

## Compact and resume

`$learn-compact` prepares a checkpoint. It does not clear the host's live context.
The assistant records the current objective, constraints, decisions, failed
approaches, open questions, source pointers, and learning position, then follows
the host procedure or starts a fresh session.

A minimal runtime request file:

```json
{
  "project": ".",
  "session_id": "demo",
  "task_id": "balance",
  "strategy": "fresh_session",
  "pointers": [],
  "known_missing_context": []
}
```

```sh
python3 -m uni_learn compact prepare --request compact-request.json --json
python3 -m uni_learn compact resume --project . --checkpoint CHECKPOINT_ID --json
```

Continue only after `safe: true`. Resume checks the checkpoint anchor, full
content hash, task/project revisions, retained facts, and pointer freshness.
Changed evidence or missing context requires review and a new checkpoint.
Codex user wide setup uses checkpoints around native `/compact`; it does not
change the compact prompt in shared Codex configuration.

## Routing and optional tools

The distributed model catalogue is empty. Until actual bindings qualify, the
router reports that the current host model should be kept. Recommendations do
not prove that the host switched models. Qualification checks availability,
tools, capacity, effort support, and comparable prices. Unknown cost is never
zero. Runtime and schema details are in [the contracts](CONTRACTS.md).

User overrides live in the application config directory as
`model-overrides.json`. A minimal file is:

```json
{"schema_version":1,"models":{},"global":{},"projects":{}}
```

RTK, graphify, Context7, and `context-mode` can be reused when available. Missing
tools leave ordinary command and source reading fallbacks. No tool or connector
is downloaded or registered by setup. GLM is a provider/model binding through a
supported assistant, not an editor profile.

## Local data and removal

On macOS/Linux, application defaults are below `~/.config/uni-learn`,
`~/.local/share/uni-learn`, `~/.local/state/uni-learn`, and
`~/.cache/uni-learn`. Absolute XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_STATE_HOME,
and XDG_CACHE_HOME override these roots. Empty or relative overrides are ignored.
On Windows, config defaults below APPDATA and separate data/state/cache roots
below LOCALAPPDATA. Explicit XDG roots work there as well.

Keep the receipt path printed by setup. Preview removal and use its digest:

```sh
python3 -m uni_learn uninstall --receipt /path/to/receipt.json --dry-run --json
python3 -m uni_learn uninstall --receipt /path/to/receipt.json --apply --plan-digest REMOVAL_DIGEST --json
```

Removal deletes only unchanged owned files or the unchanged managed instruction
block. User edits, unrelated configuration, and learning history remain. A
second removal is harmless. Use a downloaded runtime outside the installation
being removed so the executing archive is not itself deleted.

## Advanced inspection

```sh
python3 -m uni_learn doctor --project . --json
python3 -m uni_learn install --host codex --project . --dry-run --json
```

`doctor` reads local availability without installing tools or probing model
accounts. The `install` command supports a separately reviewed
`--apply --plan-digest DIGEST` flow. Its legacy manual persistent entry proposals
are distinct from automatic `setup`; ordinary installation needs only `setup`.
An explicit `--budget-model MODEL` can reuse an already installed tiktoken and
cached vocabulary verified against its hash. It never downloads a tokenizer or its assets.
A count of the file alone does not certify expanded user imports or native loading.
