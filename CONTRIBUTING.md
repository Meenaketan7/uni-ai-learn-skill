# Contributing

Use Python 3.11 or newer. The runtime and default test suite need no external
packages. Work on source files in `uni_learn/`, shared resources in `core/`, and
native adapter files in `adapters/`. Avoid putting behavior for a specific editor in
the shared runtime.

## Check a change

```sh
python3 -m unittest discover -s tests -q
python3 -m uni_learn package --output dist/uni-learn.pyz
python3 dist/uni-learn.pyz --help
```

Use temporary HOME, CODEX_HOME, and XDG directories when exercising setup. Never
use an ordinary unit test to change your actual editor settings. The existing
tests cover installation, conflicts, rollback, crash recovery, state isolation,
learning gates, checkpoints, recall, and release distribution.

Package files are allowlisted. Tests, documentation, credentials, local editor
settings, graph output, and caches must stay out of the runtime archive. A second
build from unchanged inputs must produce identical bytes.

## Release builds

The GitHub Actions workflow tests pull requests and pushes. Publication runs only
for the main branch after the test matrix succeeds. It builds a standalone
Python archive, a ZIP, installer, checksums, and build metadata. Assets are
uploaded to a draft before publication.

Each build uses its full Git commit in the release tag. The newest successful
managed release is latest; the preceding successful release is retained for
rollback. Older managed releases are pruned after publication. The publisher
recognizes both its tag pattern and body marker and preserves unrelated releases.
A failed test or upload leaves the existing published releases available.

The workflow uses the repository's GITHUB_TOKEN with contents write permission
only in its publishing job. No personal token is needed for a normal public
repository. Repository policies must allow the workflow to create and remove its
managed releases. A manual workflow run must use the main branch as well.

Inspect [the workflow](.github/workflows/release.yml) and
[publishing script](scripts/publish_release.py) before changing retention rules.
See [testing](docs/TESTING.md) for native model checks and their limits.

## GitHub repository details

The About description and repository topics are kept in
`.github/repository-metadata.json`. Apply them after pushing with GitHub CLI:

```sh
gh repo edit Meenaketan7/uni-ai-learn-skill \
  --description "Guided learning and coding skills for Codex, Claude Code, Copilot, Cursor, Gemini CLI, and more. Practice debugging, code review, and software design." \
  --add-topic agent-skills --add-topic ai-coding-assistant \
  --add-topic learn-to-code --add-topic codex \
  --add-topic claude-code --add-topic github-copilot \
  --add-topic cursor --add-topic opencode \
  --add-topic antigravity --add-topic gemini-cli \
  --add-topic kimi-code --add-topic windsurf \
  --add-topic devin --add-topic cline \
  --add-topic aider --add-topic python \
  --add-topic developer-tools --add-topic code-review \
  --add-topic debugging --add-topic learning
```

This requires GitHub CLI authentication and repository admin access. GitHub
search indexes the repository name, About description, and topics by default;
README text can be included with `in:readme`. Accurate metadata makes the project
easier to find, but search position depends on GitHub and cannot be guaranteed.
Keep topics focused on the supported tools and purpose rather than adding
unrelated keywords.
