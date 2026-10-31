---
description: "Route the implement phase with TDD, keep the ledger AC table current, tick ADO Tasks after approval."
---

# Implement with traceability

Routes the **implement** phase to the configured command, enforces test-first work, keeps
ledger section 3 (AC → test → commit/PR) current, and syncs finished tasks to ADO in one
approved batch.

## Prerequisites

- Follow `rules/hitl.md`.
- In strict mode (`"mode": "strict"` in `ado.config.json`), run `ado strict check <id> --phase implement` first and stop on exit 2, showing its findings and fixes.
- Phase routing follows `rules/routing.md`.
- `<spec dir>/tasks.md` exists (`speckit.ado.tasks`); child Tasks pushed is recommended
  (`speckit.ado.push`) so there is something to tick.

## User Input

```text
$ARGUMENTS
```

Optional story ID, then any scope (e.g. `T003-T005`). Find the story ID as described in
`rules/routing.md`.

`--workflow` (passed by the `ado-lifecycle` workflow; not a scope): skip every ADO
write. Step 4 is skipped: do not preview or apply `ado push`; say that the workflow's
`sync-preview` → `approve-sync` → `sync-apply` steps sync the ticked Tasks behind a gate.
In strict mode the workflow runs `ado strict check <id> --phase implement` itself before this command (`strict-check-implement`) and repeats `ado strict verify <id>` as a backstop (`strict-verify`). It posts the decisions you record, in any mode, behind a gate in its `decisions-loop` (never run `ado decisions <id> --post` here); `decisions-loop` is a no-op when there is no decisions file; when there is one the post happens in every mode, behind the gate.

## Steps

### 1. Load context

Read the ledger (sections 1–3) and `<spec dir>/tasks.md`. Section 3 must have one row
per acceptance criterion that appears **verbatim** in section 1 (`AC-1`…`AC-n`, in
section 1's order). If a section 1 criterion has no row yet, add the row, quoting it
word for word. Never add a row for a criterion that is not in section 1, and never
reword one. Behaviour is **covered** when an acceptance criterion states it, or when it
is the answer to a section 2 question that is ticked with an answer (the product owner's,
or `assumed: …` accepted at the workflow's `wait-po-answers` gate) and the task in
`tasks.md` that carries it cites that question as `(Q<n>)`; cite the same `Q<n>` in its test or commit.
If the work seems to need behaviour that neither covers, ask the user
before coding — it may belong in the story (update it in ADO) or out of scope.

### 2. Route the implement phase

Follow `rules/routing.md` for phase `implement`. Pass the story ID, ledger path, spec
dir, scope, and this instruction set:

- **Test first.** For each AC in scope, write or name the test, run it, and see it fail
  for the right reason before writing the code. Then make it pass.
- Tests need real assertions; a test without one does not prove an AC.
- **Record every decision.** Whenever you make a choice the story and the answered questions did not settle (a
  default, an interpretation, a deviation from the plan), or rely on an assumption, record it with
  `ado decisions <id> --add "<text>" --kind decision|assumption --why "<reason>"` (a local write into
  `<dod_path>/Decisions_<id>.md`; no names, @mentions or emails). Never only in prose, a commit message or chat, and never
  by hand-writing a `D<n>` line. The file is committed with the work.
- One task → one commit whose message names the task ID and the AC it satisfies, and ends
  with the story reference `AB#<id>` (the story id; exactly that form, so Azure Boards and
  the Azure Boards GitHub app link the commit to the story). Example:
  `git commit -m "feat: T003 count active members (AC-1) AB#<id>"`. `ado gate` lists
  commits in `<base>..HEAD` without it as `commits_without_story`.
- In strict mode make **two commits per AC** instead: a **red** commit with only the failing test files (only the story's `DoD_<id>.md`, its
  `Decisions_<id>.md` and the `tasks.md` tick may ride along, no other file), then a **green** commit with the code, and record `red <sha> → green <sha>` in the AC's
  section 3 `status` cell (step 3). `ado strict check <id> --phase pr` refuses a missing pair, a made-up SHA, a red commit that touches code and a green commit
  that is not on this branch. When the pairs are done run `ado strict verify <id>` **before you hand over** (also with `--workflow`: fix a `red-passes`, `green-fails` or `head-fails` finding here, the workflow's `strict-verify` step only repeats the run): it runs the tests at every red (must fail) and green (must pass) commit and records the result in ledger section 4 (commit the ledger).
- Tick the task in `tasks.md` (`- [x]`) when its commit lands. Never delete, reword or reorder task lines (only the
  box changes): `ado push` and `ado pr create` read that file, and a file with no tasks is treated as emptied.
- Ledger section 3 rows map only acceptance criteria that appear **verbatim** in
  section 1. Never add, reword, split, or merge AC rows. Behaviour from a cited, answered
  `Q<n>` (step 1) is built and tested but gets no row. If the work needs behaviour neither a
  criterion nor such a question covers, stop and ask the user instead of writing a row for it.

### 3. Keep ledger section 3 current

As each AC goes green, update its row:

| column | value |
|---|---|
| tests | free-form IDs: `path::test_name`, `Class.method`, or `describe > it` |
| commit/PR | the commit SHA (and PR number once it exists) |
| status | `red` while failing, `green` once the test passes on the committed code. In strict mode: `red <sha> → green <sha>` (the real SHAs of the two commits below) |

Never mark a row `green` without a passing run you saw in this session, and never
without the commit/PR value. Optionally run `ado gate <id> --offline` (read-only) to
check traceability and assertions; report its findings, do not hide them.

### 4. Sync task state to ADO ⛔

With `--workflow`: skip this step (see User Input).

At the end of the session (or when the user asks), batch every task ticked in this
session into one preview:

1. `ado push <id> --plan-out .ado/plan-push.md` (dry-run; nothing is written).
2. Show the content of `.ado/plan-push.md` to the user.
3. Wait for an explicit yes.
4. `ado push <id> --apply --expect-plan .ado/plan-push.md` (same arguments, `--plan-out` swapped
   for `--expect-plan`); report which Tasks changed.

If `tasks.md` changed between preview and apply, the CLI refuses (exit 2, `plan changed since
approval`) and writes nothing: preview again (step 1) and ask again.

### 5. Summary

Report: tasks done, AC rows green vs. total, commits made, the `D<n>` ids recorded in `Decisions_<id>.md` this session, in strict mode the `ado strict verify <id>` result, ADO sync result. Suggest
`speckit.ado.gate` and then `speckit.ado.review`.

With `--workflow`, whenever tasks remain unticked (for example you stopped on unanswered
open questions), end the summary with a final line `INCOMPLETE: <task ids>` (for example
`INCOMPLETE: T002, T003`). The review gate then tells the reviewer to reject, and
`ado pr create` refuses to open a normal PR on unticked tasks.

## HITL checkpoints

- ⛔ Step 4 is the only ADO write: one preview, one yes, one apply per batch. It is
  skipped with `--workflow`.
- Commits are local. Pushing a branch or opening a PR is not part of this command and
  needs its own explicit yes (`speckit.ado.pr`).
