# Roles: what a role changes and what it never does

Read this with `rules/hitl.md`; that file wins. A role is about who does a step. The approval rules do not change with it.

## The role is a hint

- `ado mode` records the role of the person at the keyboard in `.ado/mode.json`: one of `bpo`, `tpo`, `dev`, `qa` (`bsa` is a name for `tpo`).
- The file is agent-writable, so it steers the prompts only. `ado gate`, `ado strict`, `ado done`, `ado approvals` and CI never read it. A command that
  is for certain roles may run `ado mode --require a,b` and stop on exit 2.
- Never edit `.ado/mode.json` by hand, and never pick a role for the user: run `ado mode <role>` only when they named it in this chat.
- With `roles.map` on the target branch, picking a role needs the token identity to hold it there. A refusal is final for this session.

## What decides who may sign

- `roles.map` (identity id to roles) and `roles.signoff` (the roles whose sign-off is required: `bpo`, `tpo`, `qa`) are trust keys, read only from the
  target branch with `approvers`, `approval` and `unattended`. Editing them in a branch changes nothing. Never add an identity to them, and never
  run as a signer.
- The gates are `signoff-tpo`, `signoff-qa` and `signoff-bpo`. They are not among the 12 workflow gates. A sign-off is an approver's comment
  `/approve signoff-<role> <hash>` on the story, written by a person in the ADO web UI, where the hash covers the exact bytes signed: every Spec dir
  file except `tasks.md` for `tpo`, the QA file for `qa` and `bpo`.
- `ado done` checks every required sign-off from the comments, on those bytes. The agent will never post `/approve signoff-<role> <hash>`, never `/reject` on a human's behalf, and
  never work around a refusal. When `done` refuses with `signoff-<role>`, show the user the line it prints and wait.

## What a role never does

- It never skips a preview, a "yes", a gate, a review or a refusal.
- `implement` is never blocked by a sign-off: the sign-offs are checked at `ado done`.
- It never lets the agent act as an approver or a signer, never turns on a sign-off, and never counts as a signature.
- It never puts a token, a customer's data or a comment's text into the plan, the ledger or a file.

## Playbooks

The steps for each role are in `commands/mode.md`; the setup, the refusals and the limits are in `docs/roles.md`. The QA file `QA_<id>.md` holds only what
QA wrote; do not fill it in for QA, and never copy computed output into it, because changing one byte after the sign-off makes it stale.
