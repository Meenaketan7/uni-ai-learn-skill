# Uni Learn: guided learning for coding assistants

Uni Learn adds a learning mode to AI coding assistants. Practice programming, debugging, code review, and software design while working in a real codebase: make an attempt, get a small hint, test a prediction, or explain a concept in your own words.

It works with OpenAI Codex, Claude Code, GitHub Copilot, Cursor, OpenCode, Google Antigravity, Gemini CLI, Kimi Code, Devin Local, Cline, and Aider. The runtime is written in Python 3.11 and uses only the standard library. Your coding assistant provides the model. Uni Learn stores project preferences and task progress locally, starts in the off state, and loads teaching resources only when they are needed.

## Install

Install Python 3.11 or newer and your coding assistant. On macOS or Linux, run:

```sh
curl -fsSL https://github.com/Meenaketan7/uni-ai-learn-skill/releases/latest/download/install.py | python3 - --host codex
```

For Windows PowerShell:

```powershell
Invoke-WebRequest https://github.com/Meenaketan7/uni-ai-learn-skill/releases/latest/download/install.py -OutFile "$env:TEMP/uni-learn-install.py"; if ($?) { py -3 "$env:TEMP/uni-learn-install.py" --host codex }
```

These commands install Codex skills for your user account. For another assistant, change `--host` and choose a project directory:

```sh
curl -fsSL https://github.com/Meenaketan7/uni-ai-learn-skill/releases/latest/download/install.py | python3 - --host cursor --project /path/to/project
```

The installer verifies the downloaded runtime, checks for name conflicts and changed files, and preserves unrelated instructions and skills. After installation, start a fresh assistant session. The download links become available after the first successful release is published.

To inspect the files first, download the [latest ZIP](https://github.com/Meenaketan7/uni-ai-learn-skill/releases/latest/download/uni-learn.zip), extract it, and run:

```sh
python3 uni-learn.pyz setup --host codex
```

Run the installer again to update. Add `--channel previous` to install the preceding successful build. See [updates](docs/GUIDE.md#updates) for checking and applying updates from an existing installation.

Updates are never installed automatically. Optional notices are off by default. If enabled, Uni Learn checks for a newer release no more than once per day and reports the cached result in its normal status output.

## Use it in your assistant

Start learning with the native entry point for your assistant. Most platforms use `/learn`. Codex CLI and IDE use `$learn` or the `/skills` picker. Antigravity uses `/uni-learn`, and Kimi Code supports `/skill:learn`.

```text
/learn on
/learn Help me trace the authentication flow. Ask what I think happens before explaining it.
/learn-hint I think the token is checked in the route handler. What should I inspect next?
```

`on` and `off` set Uni Learn's saved preference for the project. They do not change the assistant's built in Plan, Ask, or Act controls. You can invoke one learning move while the saved preference is off. Start a fresh conversation after turning learning off to clear teaching instructions already loaded into the current chat.

| Command | Use |
| --- | --- |
| `learn <goal>` | Begin guided work on a task |
| `learn on` or `learn off` | Save the project's learning preference |
| `learn-hint <attempt>` | Get one small hint after trying |
| `learn-debug <problem>` | Test a hypothesis and understand a failure |
| `learn-review <artifact>` | Review work or check understanding |
| `learn-design <decision>` | Explore requirements and tradeoffs |
| `learn-recall <topic>` | Try to remember a concept before looking it up |
| `learn-compact` | Save task state before compacting or resuming |

Use the same native prefix for each command, such as `/learn-hint`, `$learn-hint`, `/skill:learn-hint`, or `/uni-learn-hint`. Saying `ship this` suspends guidance for the current task and its corrections. A new task follows the saved project preference.

## Supported coding assistants

| Assistant | Installer name | How to invoke learning |
| --- | --- | --- |
| Codex CLI and IDE | `codex` | `$learn` or `/skills`, then select `learn` |
| Codex desktop app | `codex` | Open `/` and select the enabled `learn` skill |
| Claude Code | `claude-code` | `/learn` |
| GitHub Copilot for VS Code | `copilot-vscode` | Select `/learn` from the skill menu |
| GitHub Copilot CLI | `copilot-cli` | `/learn` |
| Cursor | `cursor` | `/learn` |
| OpenCode | `opencode` | `/learn` |
| Google Antigravity | `antigravity` | `/uni-learn` |
| Gemini CLI | `gemini-cli` | `/learn` |
| Kimi Code | `kimi-code` | `/skill:learn` |
| Devin Local | `windsurf` | `/learn` |
| Cline | `cline` | `/learn` when enabled |
| Aider | `aider` | Load `.aider/uni-learn/learn.md` with `/read`, then ask to learn |

The Devin Local profile requires `--generation devin-desktop`. It targets Devin Local and does not cover legacy Cascade workflows. Antigravity reserves `/learn` for its own feature, so Uni Learn uses `/uni-learn`. Aider loads an instruction file because it does not register skills as slash commands. The [platform guide](docs/PLATFORMS.md) lists generated file paths, reload behavior, and platform specific details.

## Data and model behavior

Preferences, task progress, and checkpoints stay on your machine. Optional learning history starts off. Uni Learn does not install a background service, model account, editor extension, or extra Python package. Until a model binding is qualified, model routing recommends keeping the assistant's current model.

Guided work adds context and tool calls. In one live Codex coding sample, guided runs used more tokens and time than ordinary Codex. The [testing record](docs/TESTING.md) explains the sample and its limits. Uni Learn makes no general promise of higher accuracy or lower token use.

## Test the Codex installation

After updating, open a new Codex session in a disposable project. Confirm `learn` appears through `$learn` or the `/skills` picker. Try `$learn on`, ask for a guided explanation of a small code path, then run `$learn off` and start another fresh session. From this checkout, inspect the project's status with `python3 dist/uni-learn.pyz state show --project /path/to/project --json`; the saved `learning` value should change from `false` to `true` and back.

For a real behavior check, use a small bug with a known repair. Ask Codex to explain the invariant, record what you think the bug is, request one hint, and check that it gives a clue without editing files or revealing the full fix. Then ask explicitly for the answer and verify the repair with the project's tests. A repeatable isolated check is documented in [Testing](docs/TESTING.md#testing-the-current-codex-installation).

## Development

```sh
python3 -m unittest discover -s tests -q
python3 -m uni_learn package --output dist/uni-learn.pyz
python3 dist/uni-learn.pyz setup --host codex
```

```text
uni_learn/    Runtime, state, installation, packaging
core/         Shared learning resources and schemas
adapters/     Native assistant files and templates
registry/     Assistant, model, and integration catalogues
scripts/      Release build and publishing tools
tests/        Regression tests and learning scenarios
docs/         Usage, platform, and verification guides
```

Successful pushes to `main` publish a tested build and retain the latest two managed releases. Failed builds leave published releases intact, and unrelated releases are preserved. Build archives are attached to GitHub releases rather than committed to the source tree.

Read the [usage guide](docs/GUIDE.md), [platform guide](docs/PLATFORMS.md), [runtime contracts](docs/CONTRACTS.md), and [contributing guide](CONTRIBUTING.md). Uni Learn is available under the [MIT License](LICENSE).
