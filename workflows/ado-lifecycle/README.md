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

```bash
specify workflow run ado-lifecycle -i story_id=1001
```

## Steps and gates

Command steps run the agent once with `--workflow`, so they make no ADO writes and never stop
to ask: open decisions go to section 2 of the DoD ledger. Approval happens only at gate steps;
rejecting any gate aborts the run.

| Step | What happens | Gate |
|---|---|---|
| `pull` | Story to spec and DoD ledger | `review-spec` |
| `questions-loop` | Preview, then post the open questions to the story | `approve-questions` |
| `plan` | Plan phase | |
| `plan-questions-loop` | Preview, then post the questions the plan raised (the same comment is edited) | `approve-plan-questions` |
| `wait-po-answers` | Gate where you wait for the product owner's replies | `wait-po-answers` |
| `plan-with-answers` | Plan phase again: reads the replies into the ledger, records unanswered questions as assumed, and revises the plan | |
| `answers-questions-loop` | Preview, then post any new question that revised plan raised (it stays open until the product owner answers; `ado done` reports the story not ready meanwhile) | `approve-answers-questions`, then `review-plan` |
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
| `pr-prepare`, `pr-loop` | Build the PR body, preview, then open the PR | `approve-pr`, then `wait-po-validation` |

The run does not wait for the product owner by itself: the `wait-po-answers` gate stays open until you approve it, so
that is where you wait for replies. Approving without them accepts the plan's stated assumptions: `plan-with-answers`
(which gets `--close-assumed`) ticks each question still unanswered as `- [x] Q<n>: … → assumed: <assumption> · owner: PO`,
keeps the line in ledger section 2 for review, and so the `open_questions` gate check does not halt the run later.
Each such assumption (and every other assumption or decision the plan and implement steps make) is also recorded with
`ado decisions <id> --add` in the story's own file `<dod_path>/Decisions_<id>.md`, which `ado done` attaches to the story next to the ledger.

**Strict mode.** `strict-check-implement`, `decisions-loop`, `strict-verify` and `wait-po-validation` belong to strict mode
(`"mode": "strict"` in `ado.config.json`). The CLI answers in an advisory project, not shell conditionals: `ado strict check` and
`ado strict verify` exit 0 with `mode is advisory`, and `decisions-loop` is a no-op when there is no decisions file; when there is one the post happens in every mode, behind the gate (`ado decisions <id> --post --if-any`
exits 0 with nothing to post; its preview says so, approve to continue). Because the plan and implement prompts record `D<n>` lines in every mode,
an advisory run that has a decisions file posts the comment, behind `approve-decisions`. `implement` runs the same
check and `ado strict verify <id>` itself and fixes a finding in its own session; `strict-verify` repeats the run as the backstop; it sets `timeout: 7200`, because the engine's shell default (300 s) would kill the test runs
(one per red and green commit, plus HEAD) mid-way and skip the cleanup of their temporary checkouts. No other step sets a timeout: the `ado` calls
are local or one HTTP round trip, and a command step has none unless it captures output.
The decisions comment is posted once, after `implement`, so the product owner sees every `D<n>` the plan and implement phases
recorded. The last gate, `wait-po-validation`, comes after the PR loop (the run still ends at the open PR): the product owner
answers in the story's comment thread, and a human records each verdict in their own terminal with
`ado decisions <id> --resolve` and commit and push the resolved lines to the PR branch before the merge (the PR branch must carry the `posted:` markers and the verdicts).
In strict mode `ado done` refuses while any is open; if nothing was posted just approve. Approve it once they are all recorded; the story closes after merge.

Each `*-loop` is a preview (`ado … --plan-out`), a gate that shows that plan file, and an apply
with `--expect-plan`: the apply refuses if the plan changed since you approved it, and the loop
previews and asks again.

## Stopping, resuming and recovering

- `specify workflow status [run-id]` lists runs and where each stopped; `specify workflow resume <run-id>` continues a
  run that is `paused` or `failed`. `resume` re-runs **only the unfinished step** (and the steps after it); steps
  that already completed are not repeated, and nothing earlier is re-checked.
- A gate needs a terminal. Without one (a script, CI, an agent shell) the gate **pauses** the run instead of asking;
  run `specify workflow resume <run-id>` in a terminal to answer it.
- A run that stops at `implement` with `INCOMPLETE: T…` leaves later work undone. If you reject a gate the run is
  `aborted`, and `resume` refuses it (it takes only `paused` or `failed` runs); `review-review` is the gate you reject for
  `INCOMPLETE`. Fix the work (finish the tasks, address the review), then bring the later steps up to date by hand, in
  this order:
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
  an `aborted` one needs nothing more than `ado decisions <id> --resolve` by hand before `speckit.ado.done`.
- If the PR plan changed after you approved it, the apply refuses and the loop previews and asks again; if it kept
  changing, start a new run.

## Requirements

- Spec Kit `>=0.9.0` and a project initialized with any integration.
- The `ado` CLI 0.3.0 or later on `PATH` (the new steps need strict mode and `ado decisions --post --if-any`; with a 0.2.0 CLI
  `decisions-preview` fails on the unknown flag), and the extension, connected with `ado setup`
  (`pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.2.0` installs the 0.2.0 release, which lacks them).
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
