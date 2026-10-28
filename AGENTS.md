# Contributing instructions

Uni Learn is a Python 3.11+ runtime using only the standard library.
Keep paths and behavior specific to a host in `adapters/`; shared learning resources
live in `core/`. Package contents are explicitly allowlisted in
`uni_learn/package.py`.

Run `python3 -m unittest discover -s tests -q` after code changes. Test installs
with temporary homes and XDG directories so existing editor configuration stays
safe. Preserve unrelated user instructions during setup, upgrade, and removal.

See `CONTRIBUTING.md` for the build and release workflow.
