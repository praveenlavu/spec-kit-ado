# ado-lifecycle workflow

Runs one Azure DevOps story from spec to an open pull request with the
[`ado` Spec Kit extension](https://github.com/praveenlavu/spec-kit-ado/blob/main/README.md):
pull the story, plan, tasks, push child Tasks, implement, gate, review, open the PR. Every
change to Azure Boards or a pull request is a preview, a human gate, then an apply. The run
ends at the open PR; the story closes after merge (CI link-on-merge with `close-story`, or
`speckit.ado.done` by hand).

## Inputs

| Input | Type | Default | Meaning |
|---|---|---|---|
| `story_id` | number | required | The Azure DevOps story id. It is the only value interpolated into a shell command. |
| `integration` | string | `auto` | The integration every command step runs on; `auto` uses the one the project was initialized with. |
| `review_model` | string | empty | The model of the `review` step only (`model: "{{ inputs.review_model }}"`; empty = the integration's default, so no `--model` is passed). Recommended: a different model from the one that implemented, so the review is not the author grading itself. |
| `verdict_<gate>` (12 inputs) | string, `""`, `approve` or `reject` | empty | One input per gate, named with underscores: `verdict_review_spec`, `verdict_approve_questions`, `verdict_approve_plan_questions`, `verdict_wait_po_answers`, `verdict_approve_answers_questions`, `verdict_review_plan`, `verdict_approve_push`, `verdict_approve_decisions`, `verdict_approve_sync`, `verdict_review_review`, `verdict_approve_pr`, `verdict_wait_po_validation`. Each gate has `verdict_input:` set to its own. Empty (the default) leaves the gate as it always was: it asks at a terminal, and without one it pauses. Only `ado approvals sync --resume` sets them (below). |

```bash
specify workflow run ado-lifecycle -i story_id=1001
```

## Steps and gates

Command steps run the agent once with `--workflow`, so they make no ADO writes and never stop
to ask: open decisions go to section 2 of the DoD ledger. Approval happens only at gate steps;
rejecting any gate aborts the run. With `approval: "comment"` on the target branch, each `--apply` step of the five writes also needs the approver's
`/approve <gate> <hash>` comment on the story (`rules/hitl.md`); an apply that refuses ends the run FAILED even with a persisted verdict: start a new run.

| Step | What happens | Gate |
|---|---|---|
| `pull` | Story to spec and DoD ledger | |
| `stack-check` | Strict mode: `ado stack show --check` stops the run (exit 2) unless the stack profile is approved, matches `strict.test_command` and covers every detected root; advisory: exit 0 with notes. The fix is `speckit.ado.stack` by hand, the human's `ado stack approve --apply`, then `specify workflow resume` | `review-spec` |
| `check-review-spec` | `ado approvals check <id> --gate review-spec`: a no-op in terminal mode; in comment mode exit 2 unless an approver's `/approve review-spec <hash>` for the current `spec.md` and ledger section 2 exists | |
| `questions-loop` | Preview, then post the open questions to the story | `approve-questions` |
| `plan` | Plan phase | |
| `plan-questions-loop` | Preview, then post the questions the plan raised (the same comment is edited) | `approve-plan-questions` |
| `wait-po-answers` | Gate where you wait for the product owner's replies | `wait-po-answers` |
| `check-wait-po-answers`, `sync-po-answers` | Comment mode: `ado approvals check <id> --gate wait-po-answers` (met when every posted question has an approver's `/answer` or is due), then `ado questions <id> --sync --if-comment-mode` writes those answers into the ledger. Terminal mode: both are no-ops | |
| `plan-with-answers` | Plan phase again: reads the replies into the ledger, records unanswered questions as assumed, and revises the plan | |
| `answers-questions-loop` | Preview, then post any new question that revised plan raised (it stays open until the product owner answers; `ado done` reports the story not ready meanwhile) | `approve-answers-questions`, then `review-plan` |
| `check-review-plan` | `ado approvals check <id> --gate review-plan` (the hash covers `plan.md`, `spec.md` and ledger section 2) | |
| `tasks` | Tasks phase | |
| `push-loop` | Preview, then create or update child Tasks | `approve-push` |
| `strict-check-implement` | Strict mode: `ado strict check <id> --phase implement`; exit 2 stops the run before `implement` with the findings | |
| `implement` | Implement phase | |
| `decisions-loop` | Preview, then post the decisions and assumptions recorded in `Decisions_<id>.md` as one new comment (`ado decisions <id> --post --if-any`) | `approve-decisions` |
| `strict-verify` | Strict mode: `ado strict verify <id>` runs the tests at every red and green commit and records the result in ledger section 4; exit 2 stops the run | |
| `sync-loop` | Preview, then sync Task states | `approve-sync` |
| `gate`, `gate-check` | DoD gate; a blocking result stops the run | |
| `refresh-context` | Re-reads the story discussion so the review sees comments posted after pull | |
| `review` | Eight-plane review of the branch diff (AC fidelity in strict mode only) | `review-review` |
| `check-review-review` | `ado approvals check <id> --gate review-review` (the hash covers the newest `### Review` block, the `### AC fidelity` block in strict mode, and HEAD's full SHA) | |
| `pr-prepare`, `pr-loop` | Build the PR body, preview, then open the PR (comment mode: the apply re-checks `review-review` for the HEAD it pushes, see below) | `approve-pr`, then `wait-po-validation` |
| `sync-po-validation`, `check-wait-po-validation` | After `wait-po-validation`: `ado decisions <id> --sync --if-comment-mode` writes the approvers' `/approve D<n>` and `/reject D<n>` verdicts into the decisions file, then `ado approvals check <id> --gate wait-po-validation` (met when every posted `D<n>` has a guarded verdict). Terminal mode: no-ops | |

The run does not wait for the product owner by itself: the `wait-po-answers` gate stays open until you approve it, so
that is where you wait for replies. Approving without them accepts the plan's stated assumptions: `plan-with-answers`
(which gets `--close-assumed`) ticks each question still unanswered as `- [x] Q<n>: … → assumed: <assumption> · owner: PO`,
keeps the line in ledger section 2 for review, and so the `open_questions` gate check does not halt the run later.
Each such assumption (and every other assumption or decision the plan and implement steps make) is also recorded with
`ado decisions <id> --add` in the story's own file `<dod_path>/Decisions_<id>.md`, which `ado done` attaches to the story next to the ledger.

**Strict mode.** `stack-check`, `strict-check-implement`, `decisions-loop`, `strict-verify` and `wait-po-validation` belong to strict mode
(`"mode": "strict"` in `ado.config.json`). The CLI answers in an advisory project, not shell conditionals: `ado strict check` and
`ado strict verify` exit 0 with `mode is advisory`, and `decisions-loop` is a no-op when there is no decisions file; when there is one the post happens in every mode, behind the gate (`ado decisions <id> --post --if-any`
exits 0 with nothing to post; its preview says so, approve to continue). Because the plan and implement prompts record `D<n>` lines in every mode,
an advisory run that has a decisions file posts the comment, behind `approve-decisions`. `implement` runs the same
check and `ado strict verify <id>` itself and fixes a finding in its own session; `strict-verify` repeats the run as the backstop; it sets `timeout: 7200`, because the engine's shell default (300 s) would kill the test runs
(one per red and green commit, plus HEAD) mid-way and skip the cleanup of their temporary checkouts. No other step sets a timeout: the `ado` calls
are local or one HTTP round trip, and a command step has none unless it captures output.
The decisions comment is posted once, after `implement`, so the product owner sees every `D<n>` the plan and implement phases
recorded. Post a decision before superseding it: `--supersedes` refuses a target that is still unposted (`post D<n> first`), so the PO sees both the old line and the one that replaces it. The last gate, `wait-po-validation`, comes after the PR loop (the run still ends at the open PR): the product owner
answers in the story's comment thread. In terminal mode a human records each verdict in their own terminal with
`ado decisions <id> --resolve`; in comment mode (approvers configured, `--resolve` refused) the approvers' `/approve D<n>` and `/reject D<n>` comments are recorded by `ado decisions <id> --sync`, which the next step runs. Either way commit and push the resolved lines to the PR branch before the merge (the PR branch must carry the `posted:` markers and the verdicts).
**The `pr-prepare` ordering.** `pr-prepare` is an agent step between `check-review-review` and `pr-apply`; in comment mode `ado pr create --apply` (`pr-apply`) now re-checks `review-review` against the checkout it pushes, and the `approve-pr` plan shows the head commit. The order: in every mode the review step commits the ledger, the spec dir, the decisions file and the story's rules files in its one commit, `check-review-review` hashes the resulting HEAD, and in comment mode `pr-prepare` step 2a commits nothing: it runs `ado pr create <id> --check-committed`, which exits 2 (fixed text: commit before the review gate, run the review step again) while one of those paths is not committed. The preview and the apply refuse the same way. In comment mode `pr-prepare` also writes no ledger line (step 1a, `rule-unconfirmed`, `rule-changed`): it puts the answer in the PR body or stops with a fixed instruction to run the review step again. In terminal mode step 2a commits what is left, as before. What is covered: a commit made after the review approval (by `pr-prepare` or anyone) and a `### Review` block written after it, in the working tree or committed, refuse the apply (exit 2, nothing pushed) until the approver approves `review-review` again; every committed file is bound by HEAD's commit id, and the approver reads the review block, not the code. What is not: uncommitted files (never pushed), the PR body past the plan's length and first 120 characters, and terminal mode, where a person reads the preview. Residuals (details in `docs/configuration.md`): the review block is read from the working tree, not from HEAD; without `--push` (the workflow always passes it) and with `review_block_on` empty the PR opens from `origin/<head>`, which is not compared; `check-review-review` (and the step-2a `--check-committed`, which runs without `--base`) reads trust from `origin/HEAD` only while `pr create` uses the stricter of `--base` and `origin/HEAD`; the push sends the branch tip at push time. A refused `pr-apply` fails the run; approve `review-review` for the new HEAD and start a new run.

`ado approvals check <id> --gate <gate>` is the check for the review and wait gates in comment mode (a no-op in terminal mode); the workflow runs it after `review-spec`, `review-plan`, `review-review`, `wait-po-answers` and `wait-po-validation`. With `approvers` configured on the target branch, the product owner can also answer the posted questions with `/answer Q<n>: text` comments and `ado questions <id> --sync` writes them into the ledger (a local write; commit and push the ledger like any edit). `--resolve` is refused: `ado decisions <id> --sync` takes the verdicts from the approvers' `/approve D<n>` and `/reject D<n>` comments (a local write), then commit and push them the same way.
With `approval: "comment"` an unattended run is resumed by `ado approvals sync <run id> --resume`, scheduled on a local or persistent machine: [docs/unattended.md](../../docs/unattended.md). It sets `verdict_<gate>` (underscore names) for the paused gate and resets the other 11 inputs to empty.
In strict mode `ado done` refuses while any is open; if nothing was posted just approve. Approve it once they are all recorded; the story closes after merge.

**Business rules.** With `rules.enabled` in `ado.config.json` the same steps run business rules (see
[business rules](https://github.com/praveenlavu/spec-kit-ado/blob/main/docs/business-rules-reference.md)): no step was added, no input and no shell line changed,
and with `rules` absent or `enabled: false` the run is exactly what it was. The prompts do the work: `pull` pulls the Epic/Feature rules
(`ado rules`, GET only) before specify and `ado dod` writes `Rules from:`; `plan` tags the AC rows, raises a question for a rule no AC reflects and
records the first `ado rules <id> --confirm`; the exclusions it records after a PO answer are posted by the existing `decisions-loop`, behind
`approve-decisions`, and validated in `wait-po-validation` (terminal mode: a human with `ado decisions <id> --resolve`; comment mode: the approvers' comments, recorded by `ado decisions <id> --sync`); `pr-prepare` commits the
`Rules_<id>.md` files with the spec and the ledger. `gate-check` runs the online `ado gate`, so `rule_drift` is judged there: a drift (or
`rules-unreachable`, the gate's token cannot read the Epic/Feature) blocks the run in strict mode, and in advisory mode it is reported. A
drifted, changed or unconfirmed rule is never fixed by the run: the prompt writes one `Needs human decision` line (rule ids only), the
human re-pulls (`ado rules <id>`) and re-confirms in their own terminal, then resumes. Decisions kept on the Epic or Feature
(`ado decisions <feature id> --post`) are posted by hand, outside the workflow.

**End of the flow.** The workflow ends at the open PR; nothing in it ticks ledger section 5, so `ado done` reports the story not ready after the merge until that section is ticked. After the merge run `/speckit-ado-done <id>` (it ticks section 5 only with evidence, shows the plan, then closes the story), or tick section 5 by hand and commit it before CI `close-story` runs. In strict mode `done` additionally runs the strict `done` check. In strict mode set `close-story: true`: without it no strict `done` runs.

Each `*-loop` is a preview (`ado … --plan-out`), a gate that shows that plan file, and an apply
with `--expect-plan`: the apply refuses if the plan changed since you approved it, and the loop
previews and asks again.

## Stopping, resuming and recovering

- `specify workflow status [run-id]` lists runs and where each stopped; `specify workflow resume <run-id>` continues a
  run that is `paused` or `failed`. `resume` re-runs **only the unfinished step** (and the steps after it); steps
  that already completed are not repeated, and nothing earlier is re-checked.
- A gate needs a terminal. Without one (a script, CI, an agent shell) the gate **pauses** the run instead of asking;
  run `specify workflow resume <run-id>` in a terminal to answer it.
- `ado status <id>` reads the newest run of the story (`.specify/workflows/runs/<run-id>/state.json`, which the agent can
  edit, so it is treated as untrusted and only its status, step id and time are used, never step outputs) and prints
  whether it is paused, failed, aborted or stuck, with the exact `specify workflow resume <run-id>` when resume applies.
  After an `aborted` run (or a `completed` one while the story is still before the PR) `ado status <id> --catch-up` prints
  the commands below that your story still needs (only stages that are not met, in this order), and tells you to run
  `ado status <id>` again after each step. It is only that part of the recovery: the plan-questions round and the
  `wait-po-answers` gate are not modelled.
- A run killed with `kill -9` (or a closed laptop) stays `running` in its `state.json`, and `resume` refuses a `running`
  run (it takes only `paused` or `failed`). `ado status` says so when the last update is over 1 hour old by the server's
  clock. The documented manual fix: set `"status": "failed"` in that `state.json` by hand (the kit never edits Spec Kit's
  files), then `specify workflow resume <run-id>`; it re-runs the step that was running.
- A run that stops at `implement` with `INCOMPLETE: T…` leaves later work undone. If you reject a gate the run is
  `aborted`, and `resume` refuses it (it takes only `paused` or `failed` runs); `review-review` is the gate you reject for
  `INCOMPLETE`. Fix the work (finish the tasks, address the review), then bring the later steps up to date, in
  this order (`ado status <id> --catch-up` prints the part of it that your story still needs):
  1. `ado decisions <id> --post --if-any --plan-out .ado/plan-decisions.md` (preview), and after a yes
     `ado decisions <id> --post --if-any --apply --expect-plan .ado/plan-decisions.md`: decisions recorded after
     `decisions-loop` are not posted by a resume.
  2. Strict mode: `ado strict verify <id>`: the verification record in ledger section 4 describes the old code until you
     rerun it (`strict-check-implement` and `strict-verify` stop the run with exit 2; fix the findings, then resume). A resume
     continues at the failed step and does not re-run `decisions-loop`: decisions or overrides you recorded while fixing it
     (`ado decisions <id> --add`, `ado strict override`) are not posted by it, so post them by hand first (step 1, the same preview, yes, apply).
  3. `ado push <id> --plan-out .ado/plan-sync.md` (preview), and after a yes
     `ado push <id> --apply --expect-plan .ado/plan-sync.md`: Tasks ticked after `sync-loop` are not synced by a resume.
  4. `/speckit-ado-gate <id> --workflow` and `/speckit-ado-review <id> --workflow` (or `claude -p "…"` with the same
     text): the gate and review results in ledger section 4 describe the old code until you rerun them.
  5. `/speckit-ado-pr <id> --prepare-only`: rebuilds the PR body from the current ledger.
  6. A `paused` or `failed` run: `specify workflow resume <run-id>`; its PR loop previews, asks, and opens the PR.
     An `aborted` run: open the PR with `/speckit-ado-pr <id>` instead (it shows the preview and waits for a yes before it
     pushes or opens anything).
- A run paused at `wait-po-validation` is resumed with `specify workflow resume <run-id>` once the verdicts are recorded;
  an `aborted` one needs nothing more than `ado decisions <id> --resolve` (terminal mode) or `ado decisions <id> --sync` (comment mode) by hand before `speckit.ado.done`.
- If the PR plan changed after you approved it, the apply refuses and the loop previews and asks again; if it kept
  changing, start a new run.

## Comment mode: verdict inputs, checks and the poller

With `approval: "comment"` on the target branch (`rules/hitl.md`, `docs/configuration.md`) an approver approves from the story's comment thread instead of a terminal.
The workflow is the same file in both modes:

- **Verdict inputs.** A gate with an empty `verdict_<gate>` input behaves as before. A non-empty one (`approve` or `reject`) completes or rejects it without
  asking. A verdict **only un-pauses**: Spec Kit keeps it across resumes and anyone who can run `specify` can set it, so it is never the approval.
- **The authority is the CLI.** The five review and wait gates are followed by `ado approvals check <id> --gate <gate>` (exit 2, fixed text, unless the
  approver's comment for the current artifacts exists; a no-op in terminal mode). The seven write gates are followed by their apply, which refuses without the
  approver's `/approve <gate> <hash>` for exactly that plan. So a verdict supplied without the matching comment, or a plan changed after the approval, stops the run.
- **The poller.** `ado approvals sync <run id> --resume` (scheduled on a local or persistent machine: [docs/unattended.md](../../docs/unattended.md)) reads the gate the run is
  paused at, looks for the approver's comment, and resumes the run with
  `specify workflow resume <run id> --input verdict_<gate>=<approve|reject> --input verdict_<other>= ...`: **one `--input` per pair** (specify's option takes one value, a
  second bare value is "unexpected extra argument"), the paused gate's verdict first and the other eleven reset to empty so a stale verdict cannot pass a later gate.
  Resume restarts at the step that was paused, so the check runs next.
- **Flow.** `review-spec` pauses; the approver comments `/approve review-spec <hash>` (the poller can post the line to copy, `unattended.approval_requests`); the
  poller resumes; `check-review-spec` passes; and so on. `wait-po-answers` is met by the product owner's `/answer Q<n>: text` comments (or the question being due); `sync-po-answers`
  writes them into the ledger before `plan-with-answers`. `wait-po-validation` is met by the approvers' `/approve D<n>` and `/reject D<n>` comments; `sync-po-validation` records them in the decisions file
  (commit and push it to the PR branch before the merge; `--resolve` is refused while approvers are configured).
- **Terminal mode is unchanged, with or without approvers.** When `approval` is not `"comment"` on the target branch, `ado approvals check` exits 0 and reads nothing (not even
  a token), and the two sync steps (`ado questions <id> --sync --if-comment-mode`, `ado decisions <id> --sync --if-comment-mode`) exit 0 with a fixed note before any token,
  request or file is read, so even an admin PAT that can bypass rules changes nothing. In comment mode the sync steps run for real, and the only thing they skip is a decisions
  file that does not exist at all (nothing posted: nothing to sync). Every other refusal stops the run: a link, an oversized or non-UTF-8 decisions file, a missing or unreadable
  ledger, no approvers, a token that can bypass rules, comments cut off, an ADO error. `ado done`'s cross-check is the backstop for a decisions file nobody synced.
- **`review_model`** needs nothing else: leave it empty for the integration's default.
- **Spec Kit version.** `verdict_input` came with Spec Kit 0.15.1 (the retry/enum validation with 0.15.2), but comment mode needs **Spec Kit 1.2.1 or later**: on 0.15.1 `state.json` has no `inputs` or `execution`, so `ado status` shows no run and `ado approvals sync` refuses (fixed text), and a resume inside a loop re-runs the loop head (docs/compatibility.md has the results; CI pins v1.2.1). **A Spec Kit older than 0.15.1 ignores the key**: the gate
  stays paused, so a comment-mode poller resumes the run, the gate pauses again at the same step, and this repeats on every poll with no error. Use 1.2.1 or later for comment mode.
  `requires.speckit_version` still says `>=0.9.0` (release prep raises it to `>=1.2.1` before 0.3.0 is tagged); terminal runs work on older versions.
- **Recovery.** A check that exits 2 ends the run FAILED at that check step; get the approver's comment, then `specify workflow resume <run id>` re-runs the check
  (the poller only resumes a run that is paused at a gate). A sync step that fails is fixed the same way. A killed run stays `running`: see the recovery
  list above. A persisted verdict whose apply refuses ends the run FAILED: start a new run.
  `ado status <id>` names the step.

## Requirements

- Spec Kit `>=0.9.0` and a project initialized with any integration.
- The `ado` CLI 0.3.0 or later on `PATH` (the new steps need strict mode and `ado decisions --post --if-any`; with a 0.2.0 CLI
  `decisions-preview` fails on the unknown flag), and the extension, connected with `ado setup`
  (`pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.2.0` installs the 0.2.0 release, which lacks them). The requirement 'CLI 0.3.0 or later' is satisfied once the release bump to 0.3.0 happens: the CLI reports 0.2.0 until then, although a build of the development branch already has every verb the workflow needs.
- `git`; `gh` only when the PR goes to GitHub.

The shell steps run `ado` with your token and privileges. Read them in
[workflow.yml](workflow.yml) before the first run.

## Install

Install it with the extension through the `ado` bundle (see the
[bundle README](https://github.com/praveenlavu/spec-kit-ado/blob/main/bundle/README.md)), or on its own:

```bash
specify workflow catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/catalog/workflows.json --name ado
specify workflow add ado-lifecycle
```

Licensed under MIT ([LICENSE](LICENSE)). Changes: [CHANGELOG.md](CHANGELOG.md).
