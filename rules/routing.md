# Phase routing

`speckit.ado.pull`, `plan`, `tasks`, and `implement` do not do the phase work
themselves. They hand it to whichever command owns that phase in this project — a
stack kit's command (for example `speckit.sf.plan`) or the core Spec Kit command — and
wrap it with ADO context. Installed copy: `.specify/extensions/ado/rules/routing.md`.

The phase map lives in `ado.config.json` (written by `speckit.ado.setup`):

```json
"stack": { "kit": "sf",
           "phases": { "specify": "speckit.sf.specify", "plan": "speckit.sf.plan",
                       "tasks": "speckit.sf.stories", "implement": "speckit.sf.implement" } }
```

## Procedure (run once per routed phase)

1. **Read the map.** Open `ado.config.json` in the project root (or the file `$ADO_CONFIG`
   points to). Look up `stack.phases.<phase>` where `<phase>` is one of `specify`,
   `plan`, `tasks`, `implement`. No config file → stop and tell the user to run
   `speckit.ado.setup`.
2. **Unmapped phase** (key absent or empty): the target is core `speckit.<phase>`.
3. **Mapped phase:** the target is the mapped command, e.g. `speckit.sf.plan`. Before
   running it, confirm it is installed:
   - its extension id is the middle segment (`sf` in `speckit.sf.plan`);
   - `.specify/extensions/<id>/extension.yml` exists and lists the command under
     `provides.commands` (by `name` or `aliases`), and the extension is not disabled in
     `specify extension list`.
   If any check fails, **STOP**. Tell the user which command is mapped, what was missing,
   and the two fixes: install/enable the kit, or edit the map with `speckit.ado.setup`.
   Never fall back to the core command silently — a wrong-stack spec or plan is worse
   than no output.
4. **Announce the route** in one line before running it, e.g.
   `Routing plan → speckit.sf.plan (from ado.config.json)` or
   `Routing plan → speckit.plan (no mapping)`.
5. **Run the target** the way this agent runs Spec Kit commands (slash command or
   skill, e.g. `/speckit.plan` or `/speckit-plan`). Always pass, as part of its input:
   - the ADO story ID (`US<id>`),
   - the ledger path (`<dod_path>/DoD_<id>.md`, default `dod/`),
   - the spec dir recorded in the ledger header (`Spec dir:`), when set,
   - any user input given to the `speckit.ado.*` command.

   Never forward `--workflow` (or any other `speckit.ado` control flag, such as
   `--prepare-only`) to the routed command: those flags belong to the `speckit.ado.*`
   command only, and a stack kit's command does not know them.
6. **Let the target finish on its own terms.** A kit's own quality gates and
   checkpoints run as that kit defines. ADO checks (ledger, gate, review) run after it,
   on top — they never replace the kit's gates.
7. **Come back** to the calling `speckit.ado.*` command's remaining steps.

## Finding the story ID

Use the ID from the user input if given. Otherwise read it from the current feature's
ledger (`dod/DoD_<id>.md` whose `Spec dir:` matches the active spec dir). If neither
gives exactly one ID, ask the user — do not guess.
