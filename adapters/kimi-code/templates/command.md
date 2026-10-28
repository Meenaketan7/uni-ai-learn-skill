---
name: ${id}
description: ${description}
disableModelInvocation: true
---

# Dispatch ${id}

Installed runtime: `${runtime_command}`
Installed resources: `${resource_root}`

This is the ${id} dispatcher. Do not open teaching bodies during ordinary work while learning is off. Read preferences with `${runtime_command} state show --project ${project_arg} --json`.
For `learn on`, run `${runtime_command} learn --project ${project_arg} --command learn --argument on --json`.
For `learn off`, run `${runtime_command} learn --project ${project_arg} --command learn --argument off --json`;
include `--session SESSION_ID --task TASK_ID` when a task is active to clear its explicit activation.
Stop guidance now; a fresh session clears previously loaded teaching.

Use the host's stable session ID, or generate one UUID and carry it. For a new
objective run `${runtime_command} task start --project ${project_arg} --session SESSION_ID --objective TEXT --json`
and retain its task ID. Record an adequate existing attempt with
`${runtime_command} task update --project ${project_arg} --session SESSION_ID --task TASK_ID --attempt TEXT --json`
before requesting a hint; do not ask the learner to repeat it.
Reuse that task through corrections; a different objective
gets a new task. Substitute actual IDs and pass user text as a single argv value.

Invoke `${runtime_command} learn --project ${project_arg} --session SESSION_ID --task TASK_ID
--command ${id} --argument TEXT --json`. Use the actual user argument, including
an adequate existing attempt. Follow the returned action and open only the returned
shared resource paths below the installed resource root. The canonical selected
body is `${body}`. Select one relevant domain pack when learning is active.
The compact command preserves mode and needs the prepare/resume lifecycle;
it never claims a checkpoint write cleared live context.
Record progress with runtime task operations and obey the shared workflow.
Record both the user's existing attempt and prediction before the first
dispatch; `task update` accepts `--attempt TEXT --prediction TEXT` together.
`--changes JSON_FILE` takes a file path, not inline JSON. The flags
`--revealing-action` and `--full-answer` take no following value.
Domains are `coding`, `math-proof`, `product-system`, and `research-projects`.
Select the relevant `--domain` in the first dispatch. Use `--revealing-action`
for the next revealing action and `--full-answer` for an explicit answer request.
When the returned action is `full_answer`, read its resources and proceed;
resource selection does not need a second dispatch. Each revealing action
consumes one prediction. Use `task ship` only when the user asks to suspend
teaching, never to bypass a gate or merely to provide a requested full answer.
Native model selection is manual until an execution binding is observed.
