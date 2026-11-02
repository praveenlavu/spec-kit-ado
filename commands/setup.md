---
description: "Check tools, connect to Azure DevOps, and confirm which kit runs each lifecycle phase."
---

# Set up spec-kit-ado

Gets a project ready for the ADO lifecycle: tools present, `ado.config.json` pointing at
the right organization and project, and a **phase map** the human has confirmed. Makes
no ADO changes.

## Prerequisites

- Follow `rules/hitl.md` throughout. In particular: never ask for, accept, or echo a token.
- Run from the project root (the directory holding `.specify/`).

## User Input

```text
$ARGUMENTS
```

Consider the user input before proceeding (for example, a kit to prefer, or "reconfigure").

## Steps

### 1. Check tools

Run each and report a short table (tool, found version, required/optional):

- `python3 --version` — required.
- `ado --version` — required, `>= 0.3.0`. Missing → tell the user to install it
  (`pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.3.0`) and stop.
- `git --version` — required. Missing → stop.
- `gh --version` and `az --version` — optional; note which repo host each enables.

### 2. Connect to Azure DevOps

- If `ado.config.json` already exists with `organization` and `project`, show both
  values and skip to step 3 unless the user asked to reconfigure.
- Otherwise ask the user to run **`ado setup` themselves in their own terminal** (in
  Claude Code: `! ado setup`). It prompts for organization and project, and reads the
  PAT through a hidden prompt into the OS keychain or a 0600 file. The PAT must never
  pass through this chat.
- For a first-time user suggest `ado setup --profile starter` (`team` and `strict` add the gate lists or strict mode and ask for the optional `approvers`, which are read only from the target branch; see
  `docs/quickstart.md`); on an existing config it shows `key: old -> new` and writes only after a `y` the user types.
- `ado setup` updates only `organization` and `project`; every other key in an existing
  `ado.config.json` is preserved. If the file exists but is not a valid JSON object,
  `ado setup` refuses to touch it and exits 2.
- Verify with a read-only call only if the user gives a story ID: `ado get <id>`.

### 3. Detect installed kits

- Read `.specify/extensions.yml` (if present) and every
  `.specify/extensions/<id>/extension.yml` except `ado`.
- For each extension, list its `provides.commands` (name, aliases, description).
- Ignore extensions shown as disabled by `specify extension list`.

### 4. Propose the phase map

For each phase — `specify`, `plan`, `tasks`, `implement` — pick the candidate command
whose name or description best matches the phase (for example a kit's `stories` command
can own `tasks`). Rules:

- Propose only commands that exist in step 3. Leave a phase unmapped when nothing
  matches; unmapped phases run core `speckit.<phase>` (see
  `rules/routing.md`).
- Several kits installed → show the candidates per phase and let the human choose.
- No kits → propose an empty map.

Show the proposal as a table (phase → command → why it matched) and the exact JSON that
would be written:

```json
"stack": { "kit": "<id or empty>", "phases": { "specify": "...", "plan": "..." } }
```

### 5. Confirm and write

Wait for the human to confirm or edit the map. Do not write a guessed map. Then merge
`stack` into `ado.config.json`, keeping every other key unchanged, and offer to add the
remaining optional keys from the template with their defaults (`active_state`,
`done_state`, `task_type`, `repo_host`, `repository`, `dod_path`, `gate`,
`attachments_allow`, `review_block_on`). Show the final file content (it contains no
secrets). Never write `strict.test_command` here (the user's `ado setup --profile strict` asks for one only while no stack profile is approved): once a project has a stack profile it
comes only from `ado stack approve --apply`, which the human runs in their own terminal
(a hand edit would differ from the approved command, which strict mode reports).

### 6. Stack profile

Run `speckit.ado.stack` (pass `--workflow` if this command was given it): it detects the stack, offers an installed
kit or a kit search or a researched draft profile, and tells the human how to approve it. It installs nothing and
approves nothing. Skip it only if the human says so, and say that strict mode needs an approved profile.

### 7. Next step

Suggest `speckit.ado.constitution`, then `speckit.ado.pull <id>`.

## HITL checkpoints

- No ADO writes in this command. Writing `ado.config.json` is a local file change, but
  the phase map still needs the human's confirmation (step 5) because it decides which
  commands run later.
- If a token appears in chat at any point, do not use it; tell the user to revoke it and
  re-run `ado setup` in their terminal.
