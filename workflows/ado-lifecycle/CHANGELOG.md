# Changelog

Changes to the `ado-lifecycle` workflow. The workflow is versioned with the `ado` extension;
the full project history is in the
[repository CHANGELOG](https://github.com/praveenlavu/spec-kit-ado/blob/main/CHANGELOG.md).

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
