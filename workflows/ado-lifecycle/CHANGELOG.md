# Changelog

Changes to the `ado-lifecycle` workflow. The workflow is versioned with the `ado` extension;
the full project history is in the
[repository CHANGELOG](https://github.com/praveenlavu/spec-kit-ado/blob/main/CHANGELOG.md).

## Unreleased (0.3.0 work)

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
