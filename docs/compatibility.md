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
