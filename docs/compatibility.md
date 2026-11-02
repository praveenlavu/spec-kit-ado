# Spec Kit compatibility

This kit drives Spec Kit's workflow engine (`specify workflow run` / `resume`) and reads its `state.json`. This page says which Spec Kit versions the
behaviours it relies on were **actually tried** against, and which were not. It states results, not promises: a row without a recorded result was not run.

## What is checked

`tests/test_speckit_contract.py` drives the real `specify` CLI (never imported) in a temp project with `tests/fixtures/contract-workflow.yml`, a small
workflow with the shipped workflow id, the `story_id` and twelve `verdict_*` inputs, and a gate inside a `do-while` loop. It runs only with `specify` on
PATH **and** `SPECKIT_CONTRACT=1` (otherwise every test is skipped, so the normal suite is unchanged):

```bash
SPECKIT_CONTRACT=1 .venv/bin/python -m pytest tests/test_speckit_contract.py -q
```

| Behaviour the kit relies on | Test |
|---|---|
| A gate with no TTY pauses and the command exits 0; `state.json` says `paused` at that step | `test_a_gate_without_a_tty_pauses_with_exit_0` |
| `verdict_input` plus `resume` with the poller's own argument list (`approvals.resume_argv`: one `--input` per pair, 12 pairs) completes the gate, earlier steps do not re-run; a `reject` ends the run | `test_verdict_input_with_resume_completes_the_gate`, `test_a_reject_verdict_ends_the_run_without_running_later_steps` |
| Resume restarts at the current nested step (the failed step inside a loop, not the loop head or the gate) | `test_resume_restarts_at_the_current_nested_step` |
| A verdict stays in `inputs` and passes the same gate in a later loop iteration with no prompt; an empty `--input` resets it and the gate pauses | `test_a_persisted_verdict_passes_a_later_iteration_and_a_reset_stops_it`, `test_an_empty_input_resets_a_persisted_verdict`, `test_a_saved_verdict_survives_a_plain_resume` |
| The `state.json` keys `ado status` reads exist with the shape it expects (`run_id`, `workflow_id`, `status`, `current_step_id`, `updated_at`, `inputs.story_id` as an integer; nested step ids `<loop>:<step>:<n>`), checked through `status.read_run` and the poller's `approvals.read_run` on a real file | `test_state_json_has_the_keys_status_reads` |
| `state.json` `execution.version` is 2 | `test_execution_version_is_2` |
| `specify workflow add` (which validates) accepts the shipped `workflows/ado-lifecycle/workflow.yml`, `workflow info` lists it with its inputs and steps, and the validator really rejects a gate naming an undeclared input | `test_the_shipped_workflow_installs_and_info_lists_it`, `test_an_invalid_verdict_input_is_refused` |

## Results

**The supported floor for comment mode (the poller and `ado status`) is Spec Kit 1.2.1.** `requires.speckit_version` still says `>=0.9.0`; release
preparation (P4-18) raises it to `>=1.2.1` in `extension.yml`, `workflow.yml` and the bundle.

| Spec Kit | How it was tried | Result |
|---|---|---|
| `1.2.4.dev0` (the `specify` on the author's machine) | `SPECKIT_CONTRACT=1 pytest tests/test_speckit_contract.py`, author's run (Python 3.9 venv, macOS), before the saved-verdict test was added | **10 passed** |
| `1.2.1` (`9dca9bf4…`, the commit `ci.yml` installs) | The same tests, run by the P4-15 reviewer in a scratch environment | **10 passed** |
| `1.2.5` (the newest release at that time) | The same tests, run by the P4-15 reviewer | **10 passed** |
| `0.15.1` (`489a3d51…`, the first release with the gate's `verdict_input`, upstream changelog #3725) | The same tests, run by the P4-15 reviewer | **6 failed, 4 passed.** 0.15.1 honours `verdict_input`, but its `state.json` has no `inputs` and no `execution`, so `status.read_run` returns nothing and `approvals.read_run` refuses every run; and a resume inside a loop re-runs the loop head instead of the current nested step. The poller and `ado status` do not work on it. |

The tests were extended after those runs (the saved-verdict, `approvals.read_run` and stderr loop-condition checks): the current file ran on 1.2.4.dev0 only
(**11 passed**); the other rows are the earlier 10-test file. Nothing between 0.15.1 and 1.2.1 (for example 1.1.3, where the reviewer's source search says
`execution` first appears) was run, and nothing between 1.2.1 and 1.2.5 either; none of those is claimed. The weekly workflow re-checks 1.2.1 and the newest
release with the current tests.

## The weekly check

`.github/workflows/speckit-contract.yml` runs on a weekly schedule and on demand. It has no secrets and a read-only token, takes no input from a pull request,
installs Spec Kit by commit sha (`minimum`, v1.2.1) or by the newest `vX.Y.Z` tag (`latest`, the one entry that floats on purpose), and fails when a
contract test is skipped. It is not a PR check: a red run means "read the failing test, then this page", not "block a merge". When a row's result changes,
update the table above in the same change.

## Not tested

- Any Spec Kit between the rows above, and any older than 0.15.1.
- The real `ado-lifecycle` workflow **run** end to end: its steps call `ado` and AI integrations, so the contract tests use the fixture for run behaviour and the
  shipped file only for validation (`add` and `info`).
- Windows. The tests use POSIX `sh` steps.
- Interactive (TTY) gate prompts: the contract is the no-TTY pause.
- A killed run (`state.json` stays `running`, resume refuses it): documented in the workflow README from a manual probe (R:36), not asserted here.
- Spec Kit's behaviour with several runs in one project, or with `--input` values containing `=` or newlines.

## Calibration

Plane 8 (AC fidelity) of `speckit.ado.review` asks a model whether a test would fail if the behaviour its acceptance criterion states were wrong
(`asserts`, `partial` or `does not assert`). That reading is a model's, not proof, and models differ. `ado calibrate` measures it against a fixed set.

- **The set:** `src/ado/golden/fidelity.json` (package data, so it is in the wheel and a `pipx` install has it; there is no second copy), 12 fictional AC/test pairs (`G1`..`G12`: 4 `asserts`, 4 `partial`, 4 `does not assert`; Python, JavaScript and Apex under every label). The tricky ones are the point: a test that checks only a status code, one that asserts a different field than the AC names, an assertion inside a skipped test or a comment, a test that covers one of the AC's two conditions.
- **Where the labels come from:** labels drafted by the builder, **owner-reviewed 2026-11-02, all 12 agreed** (including G8, `partial`: its `toBeGreaterThan(25)` fails if the tax is missing, but passes for any rate or rounding). They are one reviewer's reading of the rubric, not ground truth; a model that disagrees on G1, G8 or G9 is disagreeing on a hard case.
- **How to run it** (no token, no network, and the kit never calls a model for you):

  ```bash
  ado calibrate --emit .ado/calibrate.md      # the plane 8 rubric (verbatim) + the 12 unlabelled pairs + the answer format
  # give .ado/calibrate.md to the model under test; save its reply, a JSON object {"G1": "asserts", ...}, as .ado/answer.json
  ado calibrate --score .ado/answer.json      # {agreement, false_pass, confusion, ok}; exit 0 when ok, 1 when not, 2 for a refused file
  ```

  Run it from the root of the project. The golden set always comes from the installed package. The rubric is read from `commands/review.md` when `extension.yml` sits next to it (a source checkout), else from `.specify/extensions/ado/commands/review.md` (where `specify extension add` copies the extension, so this is what the review really runs); with neither, `--emit` exits 2 and says so. From a subdirectory it also exits 2: change to the root.
- **The threshold:** agreement of at least 10 of 12 **and no false pass**. A false pass is a `does not assert` pair judged `asserts`: the strict gate blocks on `does not assert` and passes `asserts`, so it is the most dangerous error. It is not the only one that lets an untested AC through: `does not assert` judged `partial` also passes the gate (`partial` only warns). The score prints that count as `soft_pass`; it is **informational and does not affect `ok`**. **Open owner decision: count `soft_pass` as failing?** The default stays informational until the owner decides; read `soft_pass` and the `confusion` matrix as well as `ok`. Up to two such misses can sit inside an `ok`.
- **How the answer is read:** the first `{` of the text starts the answer, which must be one complete JSON object; prose or a code fence around it is fine, and anything after the object (even a second object) is ignored without a warning. Only the first object is ever judged, so emitting several objects cannot raise a score. The flip side: a `{` in prose before the answer (a model echoing the format `{"G1": "...", ...}`) is taken for the answer and refused as "not one JSON object", which costs a rerun.
- **What is refused (exit 2, fixed text, never a piece of the answer):** a path outside `.ado/` or through a symlink, an answer over 64 KiB, text without one JSON object, a duplicate id, an id outside `G1`..`G12`, a missing id (an unanswered pair is not scored as a miss), and a value that is not exactly `asserts`, `partial` or `does not assert`.
- **Recommendation:** run the review (L3) with a model other than the implementer's: the workflow's `review_model` input sets the review step's `model:`.

| Model | Date | Agreement | False passes | Result |
|---|---|---|---|---|
| (the implementer's model) | not run yet | | | |
| (a different model) | not run yet | | | |

Nothing in the table is claimed until a row has a date and counts from a real run.
