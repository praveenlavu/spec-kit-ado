# Stack kits and the phase map

`spec-kit-ado` does not write specs, plans, tasks or code itself. Four of its commands
hand that work to whichever command owns the phase in your project:

| `speckit.ado.*` command | Phase it routes |
|---|---|
| `speckit.ado.pull` | `specify` |
| `speckit.ado.plan` | `plan` |
| `speckit.ado.tasks` | `tasks` |
| `speckit.ado.implement` | `implement` |

Without a mapping, each phase runs the core Spec Kit command (`speckit.plan` and so on).
With a stack kit installed, such as a Salesforce kit `sf`, the phase map sends phases to
that kit's commands instead, and the ADO steps (ledger, gate, review, board updates) run
around it.

## The map

The map lives under `stack` in `ado.config.json`:

```json
"stack": { "kit": "sf",
           "phases": { "specify": "speckit.sf.specify", "plan": "speckit.sf.plan",
                       "tasks": "speckit.sf.stories", "implement": "speckit.sf.implement" } }
```

- `kit` is the extension ID, for your reference.
- `phases` maps any of `specify`, `plan`, `tasks`, `implement` to a command name. Leave a
  phase out (or empty) to use the core command for it.

## Building it with `speckit.ado.setup`

`speckit.ado.setup` reads `.specify/extensions.yml` and each installed
`.specify/extensions/<id>/extension.yml`, lists their commands, and proposes a command
per phase by matching names and descriptions. It shows the proposal and the exact JSON,
and writes nothing until you confirm or edit it. With several kits installed it shows
the candidates and you choose. With none, the map stays empty.

You can also edit `stack` by hand; the routing rules check it each time.

## What happens at run time

Each routing command follows [rules/routing.md](../rules/routing.md):

1. Look up `stack.phases.<phase>`.
2. If it is mapped, check the command is installed and enabled. If not, **stop** and say
   which command is missing; there is no silent fallback to core Spec Kit.
3. Announce the route, for example `Routing plan → speckit.sf.plan (from ado.config.json)`.
4. Run the command with the story ID, the ledger path and the spec directory as context.
5. Let the kit's own quality gates run as the kit defines them; the ADO gate and review
   run afterwards, on top.

## Branch names

After the specify phase, `speckit.ado.pull` runs `ado branch <id> --apply`, which renames the branch the routed
command created (Spec Kit's `NNN-…`) to `<branch_prefix>ab<id>-<slug>`, so CI can read the story from the name.
Spec Kit's `speckit.git.validate` (from its git extension) may reject an `ab<id>` name. Nothing in `speckit.ado.*` or the `ado` CLI
depends on it, so if it objects, carry on with the branch as named.

## Kits that do not write `tasks.md`

`ado push` reads one format: the Spec Kit `tasks.md` checklist (`- [ ] T001 [P] ...`). If
the mapped tasks command writes something else (for example one file per story),
`speckit.ado.tasks` writes a standard `tasks.md` from that output, one line per item,
citing the source file. You check it at the push preview before anything reaches the
board.
