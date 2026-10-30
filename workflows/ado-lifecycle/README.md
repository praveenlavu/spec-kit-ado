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
| `implement` | Implement phase | |
| `sync-loop` | Preview, then sync Task states | `approve-sync` |
| `gate`, `gate-check` | DoD gate; a blocking result stops the run | |
| `refresh-context` | Re-reads the story discussion so the review sees comments posted after pull | |
| `review` | Seven-plane review of the branch diff | `review-review` |
| `pr-prepare`, `pr-loop` | Build the PR body, preview, then open the PR | `approve-pr` |

The run does not wait for the product owner by itself: the `wait-po-answers` gate stays open until you approve it, so
that is where you wait for replies. Approving without them accepts the plan's stated assumptions: `plan-with-answers`
(which gets `--close-assumed`) ticks each question still unanswered as `- [x] Q<n>: … → assumed: <assumption> · owner: PO`,
keeps the line in ledger section 2 for review, and so the `open_questions` gate check does not halt the run later.
Each such assumption (and every other assumption or decision the plan and implement steps make) is also recorded with
`ado decisions <id> --add` in the story's own file `<dod_path>/Decisions_<id>.md`, which `ado done` attaches to the story next to the ledger.

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
  1. `ado push <id> --plan-out .ado/plan-sync.md` (preview), and after a yes
     `ado push <id> --apply --expect-plan .ado/plan-sync.md`: Tasks ticked after `sync-loop` are not synced by a resume.
  2. `/speckit-ado-gate <id> --workflow` and `/speckit-ado-review <id> --workflow` (or `claude -p "…"` with the same
     text): the gate and review results in ledger section 4 describe the old code until you rerun them.
  3. `/speckit-ado-pr <id> --prepare-only`: rebuilds the PR body from the current ledger.
  4. A `paused` or `failed` run: `specify workflow resume <run-id>`; its PR loop previews, asks, and opens the PR.
     An `aborted` run: open the PR with `/speckit-ado-pr <id>` instead (it shows the preview and waits for a yes before it
     pushes or opens anything).
- If the PR plan changed after you approved it, the apply refuses and the loop previews and asks again; if it kept
  changing, start a new run.

## Requirements

- Spec Kit `>=0.9.0` and a project initialized with any integration.
- The `ado` extension 0.2.0 and the `ado` CLI 0.2.0 or later on `PATH`
  (`pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.2.0`), connected with
  `ado setup`.
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
