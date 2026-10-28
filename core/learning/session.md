# Ordinary session bootstrap when project learning is on

Use the installed runtime and the exact bound `--project` argument shown in the
persistent entry, even if the host changes working directory. Keep one stable
host session ID; if none exists, create a UUID once and carry it through this
session. Do not reuse another session's ID.

For a new objective, run `task start --project PROJECT --session SESSION_ID
--objective TEXT --json`; carry the returned task ID. An accepted completion or
a different objective ends that task. Corrections to the same objective keep
its task ID. Read current task state with `task show` before each move.

Record an existing learner attempt with `task update --attempt TEXT`; do not ask
for it again. Record a prediction with `task update --prediction TEXT` before
running a revealing command or calculation. If the learner says “I don't know”,
ask a smaller prerequisite question. Dispatch `learn --command learn` for the
active task, then open only its returned learning resource and the one relevant
domain pack. A hint uses `learn --command learn-hint` after an attempt or
prediction; give one level, never a full solution just because level 3 was
reached. Use `--full-answer` only after an adequate attempt and an explicit
answer request. Use `--revealing-action` before exposing a test or result.

For `ship this`, run `task ship` and answer directly for this task and its
corrections. On accepted completion, run `task close`. A new objective starts
another task under the project's current mode. For `learn off`, run `learn
--command learn --argument off --session SESSION_ID --task TASK_ID` using the
bound project argument. This clears the active task's explicit activation as
well as the project preference. Stop guiding immediately and recommend a fresh
host session to clear instructions already loaded there.

Before compacting, record decisions, failed approaches, open questions and
learning position in task state, then use `compact prepare`. Execute the host's
verified native compact procedure or start a fresh session, and call `compact
resume` before continuing. Stop on unsafe resume and repair the missing state
or changed evidence first.
