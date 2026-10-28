# Testing

## Automated checks

```sh
python3 -m unittest discover -s tests -q
python3 -m uni_learn package --output dist/uni-learn.pyz
python3 dist/uni-learn.pyz --help
```

The suite uses temporary homes, application directories, and projects. It checks
learning gates, isolated task state, checkpoint integrity, history/recall,
routing eligibility, all adapter profiles, installation ownership, rollback,
crash recovery, packaging, and release distribution. The latest local gate on 2026-10-27 was 223 passing tests on macOS with Python
3.14, with no skips. The GitHub Actions matrix is the
source of results for actual Linux and Windows runners; local path fixtures do
not establish those platforms passed.

The final archive smoke check exercised setup, reinstall, the printed
native entry point, runtime mode on/off, and removal for all twelve profiles in
temporary homes/projects outside the checkout. Archive, manifest, and asset hashes
matched, and the runtime rebuild was identical in bytes. This checks the distribution
and generated integrations without launching each editor.

Package builds must be reproducible and contain no tests, development docs,
credentials, caches, or personal editor settings. Build artifacts stay in ignored
`dist/` and GitHub release assets.

## Native loading checks

These optional checks need the installed Codex CLI. They use isolated homes and
a dummy loopback provider; they do not send model requests:

```sh
python3 -m tests.native_global_codex_check --output /tmp/codex-global.json
python3 -m tests.native_codex_check --output /tmp/codex-project.json
```

An optional `--budget-model MODEL` uses an already prepared local tokenizer.
Neither a token count nor native discovery proves answer correctness.

## Codex and update checks: 2026-10-27

Codex CLI 0.163.0 found and enabled all seven skills in isolated global and
project installations. Native prompt rendering loaded the managed instructions,
preserved existing instructions, and left teaching bodies unloaded for ordinary
requests. The global check used two projects and verified their preferences
remain separate. These loading checks did not send a model request.

A separate authenticated hint request used the current project dispatcher on
the transfer scenario. Codex returned one clue about tracking the dictionary
before the missing account lookup, left the source unchanged, and used one
successful learning dispatch. The task remained active at hint level one with
shipping bypass off. All ten shell calls succeeded. The request took 49.75
seconds and reported 207,307 input tokens, including 186,368 cached tokens,
1,353 output tokens, and 21 reasoning tokens. Configuration selected
`gpt-6.1-sol` at medium reasoning; the event stream did not independently identify
the executing model. Existing global 0.2.0 instructions also loaded, so this was
a project dispatcher smoke check rather than a clean global upgrade benchmark.

Updater verification used mocked GitHub responses and real archive subprocesses
in temporary homes. An actual 0.2.0 archive upgraded to 0.3.0, then the previous
channel restored 0.2.0. Personal instructions and every learning and task state
file remained unchanged. The checks also rejected corrupt runtime bytes,
incorrect embedded versions, and foreign asset URLs before execution. Notice
coverage includes disabled operation without network or writes, cached failures,
concurrent calls, corrupt preferences, abandoned cache locks, fork selection,
and commands for paths containing spaces.

These checks do not certify remote publication, every editor, or general answer
accuracy. Public update downloads require a successful published release.

## Live Codex sample: 2026-10-23

Three fresh temporary Git projects used the installed global Uni Learn 0.2.0
skills with actual authenticated Codex CLI 0.163.0 inference. The configured
model was `gpt-6.1-sol` at medium reasoning; the JSONL trace did not independently
report the executing model ID. The shared coding scenario is
[tests/scenarios/coding.json](../tests/scenarios/coding.json). Only learner
material was supplied; reviewer assertions were applied afterward.

| Condition | Behavior cases | Input tokens | Cached input | Output tokens | Time | Shell calls |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Ordinary Codex | 7/7 | 121,152 | 103,808 | 1,190 | 48.49 s | 8 |
| Explicit guided full answer | 7/7 | 259,037 | 228,992 | 2,497 | 75.64 s | 18 |
| Explicit hint | Source unchanged; one clue | 148,370 | 132,736 | 1,260 | 52.74 s | 12 |

Both repairs preserved the entire dict on rejection, total funds and nonnegative
balances on success, return identity, and valid same account behavior. The hint
pointed to mutation order without revealing the answer.

Trace review also found behavior problems: one invalid domain retry and an
unnecessary second revealing dispatch followed by shipping bypass. The 0.3.0
instructions enumerate valid domains, select the domain on the first dispatch,
explain that a returned full answer needs no second dispatch, and reserve shipping
for an explicit request to suspend teaching. Correct final code alone did not
count as a complete learning policy pass.

These are observations from one run on one coding scenario, including ambient Codex
instructions/tools and prompt caching. The guided run used more tokens and time;
there is no general efficiency or accuracy claim. Dollar cost, per resource token
attribution, other domains, and every editor's live behavior were not measured.

## Revised dispatcher retest

Two fresh project installations used source version 0.3.0 with the same prompts.
The full repair again passed 7/7 cases, kept the task active, and left shipping
bypass off. The hint used one successful dispatch, returned a single clue, and
left source unchanged. Neither run used the invalid domain name or shipping.

| Condition | Input / cached input | Output | Time | Shell calls |
| --- | ---: | ---: | ---: | ---: |
| Guided full answer | 312,028 / 276,224 | 3,626 | 108.78 s | 20 |
| Hint | 203,833 / 183,424 | 1,296 | 45.42 s | 10 |

The full answer run recovered from two CLI mistakes: treating a boolean flag as
if it took text and supplying inline JSON to a file path option. It also delayed
recording an existing prediction. The final instructions and CLI help now state
these argument types and show recording attempt/prediction together. The retest
still establishes no efficiency improvement. The older global entry also loaded
alongside project setup, so this was not a clean global upgrade comparison.

## Final full answer check

A final isolated project installation used the clarified instructions with the
same learner prompt. It passed all 7 contract cases, recorded the existing
attempt and prediction, and returned `full_answer` through one successful
dispatch. The task remained active with shipping bypass off. All 15 shell calls
succeeded; no invalid domain, flag misuse, or shipping call occurred.

Observed usage was 155,378 input tokens (138,112 cached; 17,266 uncached), 2,268
output tokens, and 61.14 seconds. This is a useful smoke check, not evidence of
general savings: the ordinary baseline used fewer total input/output tokens and
completed sooner, and the runs were not a controlled repeated benchmark.

The release matrix uses Python UTF-8 mode so UTF-8 fixture files are read
consistently on Windows. A separate subprocess regression explicitly forces
cp1252 standard output and verifies Unicode project paths and setup still work.

## Repeating a live check

Use a disposable project and temporary XDG roots. Keep real credentials out of
fixtures and reports. For a bounded authenticated run, capture structured events
with `codex exec --ephemeral --json` and record the final answer, command results,
`turn.completed` usage, elapsed time, configured model, and host version. This
uses your assistant account; the default test suite never launches it.

Supply only the scenario's learner material. Check the answer using its
reviewer only assertions after the run. For hints, also verify no premature
solution, source changes, hint jump, prediction bypass, or unsolicited shipping.
Use identical prompts, model settings, tools, and stated cache conditions for
comparisons; show cached and uncached input separately. Report unavailable
measurements as unavailable. `tests/evaluation.py` validates supplied comparison
records but does not run or grade a model automatically.

Before certifying an editor version, check discovery, fresh off mode, persisted
on/off across sessions, one task activation, shipping corrections, successive
hints, compact/resume at hint level two, optional logging and bounded recall,
two isolated sessions, reinstall, and removal with an edited owned file. Record
what is observed and what remains unknown. A generated adapter is not a live
certification.

## Testing the current Codex installation

The current source build can be installed globally before it is published. From
the repository root, create the runtime archive and run Codex setup:

```sh
python3 -m uni_learn package --output dist/uni-learn.pyz
python3 dist/uni-learn.pyz setup --host codex
```

Setup updates the user installation. It will preserve unrelated instructions and
stop if an owned file was edited. Read the setup result and resolve any reported
conflict before continuing. Close Codex and open a fresh session in a disposable
project directory so the new skill and dispatcher are loaded.

In Codex CLI or the IDE, select `learn` from `/skills` or invoke `$learn`. Codex
does not use `/learn` as its skill invocation in these surfaces. Try these steps:

1. Ask Uni Learn to help repair a transfer function with a bug: it subtracts
   money from the source before it checks whether the target exists. Require it
   to state an invariant and ask for your prediction before editing.
2. Record your attempt and prediction, then invoke `$learn-hint` with that
   attempt. The answer should give one small clue and leave the source unchanged.
3. Ask explicitly for the full answer. The runtime should not require a second
   revealing dispatch, and the assistant should not mark the task as shipped.
4. Check the final code for these cases: a rejected transfer leaves the entire
   dictionary unchanged, a successful transfer preserves the total and
   nonnegative balances, the same dictionary is returned, and a transfer to the
   same account is a no operation.
5. In another terminal, run:

   ```sh
   python3 dist/uni-learn.pyz state show --project /absolute/path/to/disposable/project --json
   ```

   Confirm `learning` changes after `$learn on` and `$learn off`. Start a fresh
   session after turning it off.

For an update check, first enable optional notices and inspect the status:

```sh
python3 dist/uni-learn.pyz updates enable
python3 dist/uni-learn.pyz updates check
python3 dist/uni-learn.pyz updates status
```

When an update is available, use the receipt path from Codex setup:

```sh
python3 dist/uni-learn.pyz update --receipt /path/to/receipt.json
```

An update installs the published release. The current source build is not
published until a successful release workflow runs. To try unpublished changes,
rebuild the local archive and rerun `setup` as shown above. Update checks contact
GitHub only when requested or when optional notices are enabled.

## Platform command research

The [platform reference](PLATFORMS.md) links primary documentation checked on
2026-10-26 for all twelve profiles. The research corrected OpenCode's missing slash
command wrappers, current Devin Local's slash invocation, Antigravity's built in
command collision, and the overbroad Codex CLI/desktop statement.

Regression coverage verifies all generated profiles, OpenCode wrapper ownership
through setup/reinstall/removal and native skill collisions, plus Antigravity's
native name collisions and owned upgrades without path migration. The latter
also checks failure rollback, repeat setup, edited file preservation, and removal.
These are deterministic checks; no new live certification for other editors is
implied by researching their documentation.
