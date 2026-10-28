${core_entry}
At each turn run `${runtime_command} state show --project ${project_arg} --json`.
Report any `update_notice` once per session. Update only when the user requests it.
If learning is on, open `${resource_root}/core/learning/session.md` and follow
its ordinary task bootstrap. While off, leave that teaching resource unopened
unless the user explicitly invokes a learning move.
