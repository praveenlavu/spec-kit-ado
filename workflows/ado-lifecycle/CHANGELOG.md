# Changelog

Changes to the `ado-lifecycle` workflow. The workflow is versioned with the `ado` extension;
the full project history is in the
[repository CHANGELOG](https://github.com/praveenlavu/spec-kit-ado/blob/main/CHANGELOG.md).

## 0.3.2 — 2026-11-09

- Version string only; no step, input or gate changed. The release fixes the `ado` CLI behind the `pr-loop` steps: the PR plan now names the head commit in every approval mode, so a commit added while the run is paused at `approve-pr` makes the apply refuse (it ends the run FAILED: start a new run). Use the CLI at v0.3.2 (`pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.3.2`).

## 0.3.1 — 2026-11-08

- Security fix: the seven preview, gate, apply loops (`questions-loop`, `plan-questions-loop`, `answers-questions-loop`, `push-loop`, `decisions-loop`, `sync-loop`, `pr-loop`) now run one pass (`max_iterations: 1`). Before, when an apply refused a plan that had changed after approval, the loop previewed again and a verdict already supplied with `specify workflow resume --input verdict_<gate>=approve` (which stays set in the run inputs) passed the second gate with no prompt, so the new plan was applied without a new human decision. Now the refused apply ends the run FAILED through the `*-applied` step (start a new run). Comment mode was already guarded by the approver's comment for the exact plan. Found by an automated review on the Spec Kit workflow catalog PR; pinned by real-engine tests in `tests/test_speckit_contract.py`.
- The seven `*-applied` messages now read "plan changed after approval; start a new run". No step, input or gate was added or removed.

## 0.3.0 — 2026-11-03

- Comment-mode wiring (P4-14, D§4.2): twelve string inputs `verdict_<gate>` (underscore names, `enum: ["", "approve", "reject"]`, default `""`), each gate gets `verdict_input:` set to its own, so `ado approvals sync --resume` can un-pause a gate (one `--input` per pair on `specify workflow resume`); an empty input leaves a gate exactly as before. Seven new shell steps: `check-review-spec`, `check-review-plan`, `check-review-review`, `check-wait-po-answers` (`ado approvals check <id> --gate <gate>`, a no-op in terminal mode), `sync-po-answers` (`ado questions <id> --sync --if-comment-mode`, after the `wait-po-answers` check and before `plan-with-answers`), `sync-po-validation` (`ado decisions <id> --sync --if-comment-mode`, after the `wait-po-validation` gate) and `check-wait-po-validation`. `--if-comment-mode` makes the two sync steps no-ops (exit 0, a fixed note, no token or request) unless `approval` is `"comment"` on the target branch, so terminal mode is unchanged even with approvers configured; in comment mode only a decisions file that does not exist at all is skipped, every other failure stops the run. The `wait-po-validation` gate message names `--sync` for comment mode. New input `review_model` (default empty) feeds the `review` step's `model:`. Only `inputs.story_id` is interpolated into shell. `verdict_input` exists from Spec Kit 0.15.1 (older ignores it, so the poller re-pauses at the same gate on every poll), but the poller and `ado status` need 1.2.1 or later (0.15.1's `state.json` has no `inputs` or `execution`: they refuse or find no run); `requires.speckit_version` is unchanged until release preparation (P4-18 raises it to `>=1.2.1`).
- In `approval: "comment"` mode the `--apply` steps of `questions`, `push`, `decisions`, `pr create` and `branch --link` need the approver's `/approve <gate> <hash>` comment (P4-12a); the step list is unchanged, and an apply that refuses ends the run FAILED (start a new run).
- New shell step `stack-check` (`ado stack show --check`) after `pull` and before `review-spec`: in strict mode it stops the run with exit 2
  unless the stack profile is approved, matches `strict.test_command` and covers every detected root; in an advisory project it exits 0 with notes.
  The fix is the `speckit.ado.stack` command run by hand (the workflow has no step for it: it asks the human questions and the human approves in a terminal).

- Strict-mode steps (no shell conditionals; the CLI answers in an advisory project: `strict-check-implement` and `strict-verify` are no-ops, and `decisions-loop` is a no-op when there is no decisions file; when there is one the post happens in every mode, behind the gate): `strict-check-implement`
  (`ado strict check <id> --phase implement`) before `implement`; `decisions-loop` after `implement` (preview, gate `approve-decisions`,
  apply with `--expect-plan`, `decisions-applied`) posts the decisions recorded in `Decisions_<id>.md` as one new comment with
  `ado decisions <id> --post --if-any`; `strict-verify` (`ado strict verify <id>`) before `sync-loop`; and a last gate
  `wait-po-validation` after the PR loop, where you wait for the product owner's verdicts, recorded by a human with
  `ado decisions <id> --resolve` (then commit and push the resolved lines to the PR branch before the merge; in strict mode `ado done` refuses while any is open). `strict-verify` sets `timeout: 7200`. The run still ends at the open PR.
- Needs the 0.3.0 `ado` CLI (`--if-any`, strict mode).

## 0.2.0 — 2026-10-29

- New `integration` input (default `auto`), passed to every command step;
  `requires.integrations.any` lists the integrations it was written for.
- After `pull`, and again after `plan`, the open questions are posted to the story, each behind its own gate
  (`pull` and `plan` write what the product owner must answer as `owner: PO` `Q<n>` lines, the only thing that is posted).
  A `wait-po-answers` gate then a second `plan` run (`plan-with-answers`) read the replies into the ledger and revise the plan.
  That second run gets `--close-assumed`: a question the product owner left unanswered is recorded as `assumed: <assumption>`
  (ticked, kept in section 2), so approving the gate without replies does not halt the run at `gate-check`.
- Every write (questions, task push, task sync, PR) is a preview, gate and apply loop: the apply
  passes `--expect-plan` and refuses a plan that changed since approval, and the loop re-previews
  and re-asks.
- The gate step is `speckit.ado.gate`, followed by a `gate-check` that stops the run on a
  blocking result; prepared PR files are cleared before `pr-prepare`.
- Gate and review steps get `--workflow` and never ask; the review gate says to reject a run
  that ends with `INCOMPLETE: <tasks>`.

## 0.1.1 — 2026-10-27

- Version bump with the 0.1.1 extension; no workflow changes.

## 0.1.0 — 2026-10-27

- First release: pull, plan, tasks, push, implement, gate, review and PR, with preview, gate
  and apply for every board or PR change. Command steps get `--workflow` and make no ADO writes.
  The run ends at the open PR.
