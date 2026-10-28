---
name: ${id}
description: ${description}
---

# ${id}
Runtime: `${runtime_command}`
Resources: `${resource_root}`
${project_resolution}

Run the following verbs using the runtime above. Keep stable SESSION_ID and TASK_ID.
- For on/off: `learn --project ${project_arg} --command learn --argument on` (or `off`). Include session/task IDs on off when a task is active.
- For a new objective: `task start --project ${project_arg} --session SESSION_ID --objective TEXT --json`; retain its returned task ID through corrections.
- Record supplied attempts and predictions before the first dispatch: `task update --project ${project_arg} --session SESSION_ID --task TASK_ID --attempt ATTEMPT_TEXT --prediction PREDICTION_TEXT`. Omit either option only when the user supplied none; do not ask them to repeat existing work. For structured changes, `--changes JSON_FILE` takes a file path, not inline JSON.
- Dispatch: `learn --project ${project_arg} --session SESSION_ID --task TASK_ID --command ${id} --argument TEXT --json`.
Pass actual user text as one argv value. Follow the returned action and load only its returned shared resource paths; the selected body is `${body}`. Select only the relevant domain pack. Ordinary work while learning is off leaves teaching bodies unopened.
Choose `--domain coding`, `math-proof`, `product-system`, or `research-projects` in the first dispatch. `--revealing-action` and `--full-answer` are boolean flags with no following value. Include the first for the next revealing action and the second for an explicit answer request. A returned `full_answer` authorizes the answer; read its resources and proceed without dispatching again. Each revealing action consumes one prediction; resource selection does not need a second dispatch.
Use `task ship` only when the user asks to suspend teaching (for example, "ship this"). A request for a full answer keeps learning active; never use shipping to bypass a returned gate. Accept completion with `task close`. For compact preserve mode, use prepare/resume and the reported host procedure; saving a checkpoint never proves native context clearing. Report unsupported model controls honestly.
