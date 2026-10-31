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

## Detection

`ado stack detect [--repo DIR]` tells you which stacks the repository holds and why. It is read-only and local (no token, no
network, no file written) and, in this release, advisory: no other verb acts on it yet. It reads the **files tracked at `HEAD`**
(`git ls-tree -r -z`), so it is the same locally and in CI and skips `node_modules`, virtual environments and build output;
untracked, staged-only and working-tree changes are ignored. A repository with no commit gives `outcome: "unknown"` and
`note: "commit first"`.

```json
{"outcome": "multiple",
 "stacks": [{"id": "python", "root": "backend", "confidence": "high", "evidence": ["backend/pyproject.toml"],
             "hints": {"pytest": true, "src_layout": false}},
            {"id": "typescript", "root": "web", "confidence": "high", "evidence": ["web/package.json", "web/tsconfig.json"],
             "hints": {"package_manager": "npm", "test_script": true}}],
 "domain_hints": []}
```

`outcome` is `detected` (one stack with a high marker), `multiple` (two or more, each with its own root, so
`backend/pyproject.toml` plus `web/package.json` is two stacks) or `unknown` (none). Only fixed file names are matched, at most
3 path segments deep (`a/b/pyproject.toml` counts, `a/b/c/pyproject.toml` does not):

| Stack id | High confidence | Medium (alone: listed, never `detected`) | Hints |
|---|---|---|---|
| `python` | `pyproject.toml` | `setup.py`, `setup.cfg`, `requirements*.txt`, `Pipfile` | `pytest` (`[tool.pytest` in `pyproject.toml`, `pytest.ini`, `conftest.py`, a `pytest` line in a requirements file), `src_layout` |
| `node`, `typescript` | `package.json` (`tsconfig.json` beside it makes it `typescript`) | none | `package_manager` (`npm`, `pnpm`, `yarn` from the lockfile, or `ambiguous`), `test_script` (`scripts.test` present) |
| `dart`, `flutter` | `pubspec.yaml` (a `sdk: flutter` line makes it `flutter`) | none | none |
| `salesforce` | `sfdx-project.json` | a `force-app/` directory | `lwc_jest` (`@salesforce/sfdx-lwc-jest` in the `package.json` beside it) |
| `java` | `pom.xml`, `build.gradle`, `build.gradle.kts` | none | `build_tool` (`maven`, `gradle` or `both`) |
| `go`, `rust`, `dotnet` | `go.mod`, `Cargo.toml`, `*.csproj` / `*.fsproj` / `*.sln` | none | none |
| `c-cpp` | `CMakeLists.txt`, `meson.build` | `configure.ac`, `Makefile` | none |

- **Precedence at one root:** `sfdx-project.json` absorbs the `package.json` beside it (the LWC Jest harness, no separate Node
  stack); a `pubspec.yaml` absorbs the Gradle projects under its `android/` directory; `typescript` replaces `node`. Otherwise every
  root with a high marker is its own stack (a medium marker beside a high one of the same stack is only added to its evidence).
- **Nested roots of the same stack are one stack:** a high-confidence root inside another high-confidence root of the same stack
  (`node` and `typescript` are one family, `dart` and `flutter` too) folds into the nearest ancestor and its manifests are added to the
  ancestor's evidence (at most 10 entries are printed). So npm workspaces, Maven multi-module, Gradle multi-project and Cargo
  workspaces are ONE stack, while `backend/pyproject.toml` plus `web/package.json` (different stacks), or two sibling roots of the same
  stack with no common root, stay separate and are `multiple`. A node app inside a Python repo is still `multiple`.
- **Domain hints, not stacks:** `domain_hints` lists `media` when a manifest that was read (`pyproject.toml`, `setup.cfg`, `Pipfile`,
  `requirements*.txt`, `package.json` dependencies, `CMakeLists.txt`) names `ffmpeg`, `libav*`, `av`, `ffmpeg-python`, `gstreamer`
  or `opencv*` as a word (comment lines are skipped; a hint can be a false positive). A video project is its underlying language
  plus this hint.
- **Pega and Appian are `unknown`:** the application lives in the platform's rule database, not in files, so no file marker is
  shipped for them. The one hint-only guess: a `package.json` that depends on an `@pega/*` package (a Constellation front end) sets
  `hints.pega: true` on its `node` or `typescript` stack and `pega` in `domain_hints`. It is a guess to confirm, not a detection;
  name the stack yourself.
- **Untrusted paths:** in CI a PR controls every path, so a root or evidence path is printed only when it is 1 to 200 characters of
  `A-Za-z0-9._/-`; anything else (a space, a control character, a non-UTF-8 byte, a longer path) is printed as `<path #n>`. Ids,
  evidence and hints come from the marker table and fixed words, never from file contents. A manifest is read only from the commit,
  only when it is a regular file of at most 1 MiB (never a symlink), at most 32 per run (and, separately, 32 `pubspec.yaml` files to tell
  `flutter` from `dart`). A manifest that could not be read as asked (the budget is used up, git fails, or a `package.json` is not a JSON
  object or repeats a key) leaves its hints `false` and adds `"note": "hints incomplete"` to the output. Only SHA-1 repositories are read (a
  64-hex object id is `cannot read the commit`). The commit is `HEAD` (or a full 40-hex id or branch through the API; never a tag); git runs
  with strict mode's hardened environment but keeps your global git config, so `safe.directory` works for a checkout owned by another user. A repository with more than 20000 marker-named
  files is refused (`too many files to inspect`). Exit 2 with a fixed text when git cannot be read. The same commit gives
  byte-identical output. This is process help, not a security boundary.

## Kits that do not write `tasks.md`

`ado push` reads one format: the Spec Kit `tasks.md` checklist (`- [ ] T001 [P] ...`). If
the mapped tasks command writes something else (for example one file per story),
`speckit.ado.tasks` writes a standard `tasks.md` from that output, one line per item,
citing the source file. You check it at the push preview before anything reaches the
board.
