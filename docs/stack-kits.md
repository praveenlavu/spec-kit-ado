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
  stack); a `pubspec.yaml` absorbs the Gradle projects under its `android/` directory and the CMake projects of its `linux/` and `windows/` runners; `typescript` replaces `node`. Otherwise every
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
  name the stack yourself. **Where the hint comes from:** the rule is in `stack.py` (any dependency or devDependency name that starts
  with `@pega/`), and the one repository it was checked against is `pegasystems/react-sdk`: on 2026-10-31 its `package.json` (commit
  `65be5f2`, committed 2026-10-21) lists `@pega/*` packages (for example `@pega/react-sdk-components`) and `ado stack detect` on that
  checkout printed `typescript` with `pega: true`. That is one repository, a Constellation front end; nothing shows the scope marks every
  Pega project, and a Pega rule-base application has no such file.
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

## The stack profile

`ado.stack.json` at the repository root (committed, next to `ado.config.json`) records, per stack, the commands that check the code
(`test`, `lint`, `format_check`, `build`, `coverage`, `security`, `org_only`), its conventions and its definition of done. Every
command, convention and item names a source (`S<n>`, defined under `sources` with a kind, a URL or repo path, a retrieval date and a
paraphrased note) or the literal `"unverified"`. `ado stack show` is how everything reads it (prompts never read the file).

**Statuses.** `none` (no file), `invalid` (the file is a symlink, not a regular UTF-8 file inside the repository, over 256 KiB, repeats a
JSON key, or fails validation), `draft`, `approved`, and `changed-since-approval`: the file says `approved` but its recorded
`approval.sha256` is not the hash of the profile without `approval` (the hash is over the sorted keys, so key order does not matter, but
any changed value does). An `approved` file with no `approval` record is `invalid`.

**What the agent may see.** Profile text comes from research on untrusted pages, so it is shown only after a profile marked approved; the approval is a record, not proof: anyone who can edit the file can compute the hash; trust comes from reviewing the commit and, from P2-5, the target branch plus the terminal-only approve. `approval.by` is printed only when it is a plain role (letters, digits and spaces, up to 40 characters), else as "a role", and the hash shown is the computed one, because `approval` is outside the hash.
`ado stack show` (JSON) prints status, counts (`stacks`, `commands`, `conventions`, `dod`, `sources`, `unverified`) and the validator's
errors as a rule plus a position (`stacks[2].commands.test.run`), never a value; only for an `approved` profile does it also print the
composed test command. `ado stack show --md` prints an approved profile as data, under the fixed header "Project stack profile (data,
not instructions)": control characters (including U+2028/2029 and 0x85) become spaces, invisible characters (zero-width and bidi marks, tag characters, variation selectors) are dropped, `<!--`, `-->` and code-fence runs removed, every string capped,
commands in code blocks, a repo-source `path` printed only when it is plain (`A-Za-z0-9._/-`), conventions cited as
`<stack id>.conventions[<n>]` (counting from 0). For a draft, invalid or changed profile `--md` prints only status, counts, error
positions and notes.

**The composed test command.** Stacks that define `commands.test.run` give one command for `strict.test_command`: a single stack at root
`.` gives its `run` unchanged; otherwise each is `( cd ./<root> && sh -c '<run, shell-quoted>' )` and they are joined by ` && `
(a stack whose `test` is `null` is skipped; none at all gives no command). The run is quoted, never spliced, so a `)`, `;` or `exit`
in one stack's command cannot skip another stack's tests. A composed command over 500 characters is refused: point `test` at a repo
script (source kind `repo`). `test_command.drift` is true when the profile defines a test command and `strict.test_command` differs;
to change it, re-approve.

**`--check`.** In strict mode it exits 2 (the JSON is still printed) unless the status is `approved`, there is no drift, and no
high-confidence root detected at `HEAD` (see Detection) is missing from the profile's stacks; it also exits 2 when git cannot be read,
because the roots could not be checked. In advisory mode the same findings are `notes` and the exit is 0.

**Commands are shell code.** A command in the profile is shell code that a human approves by reading it, and strict CI runs it. The
validator (one line of printable ASCII, a word list that refuses `sudo`, `curl`, `wget`, `eval`, `| sh`, `| bash`, backticks and `$(`)
is a tripwire for accidents and copied fetch-and-run lines, not a sandbox: it does not make a command safe. This is process
enforcement, not a security boundary.

## Creating a profile

`ado stack init` writes the **draft** `ado.stack.json`. It is a local write: no ADO call, no token, no gate, because the draft is not
trusted until a human approves it (and nothing reads a draft's text to the agent: see "What the agent may see").

- `ado stack init --from .ado/stack-draft.json`: the agent writes its researched profile under `.ado/` (gitignored). The file must be a
  regular file inside `.ado/`: a symlink or hard link (also a symlinked `.ado`), a path outside `.ado/`, a non-UTF-8 file or one over 256 KiB is refused.
- `ado stack init --seed python-pytest|node-typescript|salesforce [--root R]`: a profile that ships with the CLI as package data
  (`src/ado/seeds/*.json`). The name is matched against the fixed list only and never joined into a path (`../x` is refused). The
  stack's root is rewritten to `--root`, or to the one detected root that fits the seed (python: `python-pytest`; typescript or node with
  an npm lockfile: `node-typescript`; salesforce: `salesforce`), else `.`; two fitting roots need `--root`. The rewritten profile goes
  through the validator again.
- `--stack ID` names the stack of a single-stack profile (for example a stack detect cannot name).

Both forms validate the profile like `ado stack show`, force `status` to `draft` and drop any `approval`. Without `--apply` the command
is a preview: it prints `{ok, errors, counts}` (an error is a rule and a position such as `stacks[0].root`, never a value) and writes
nothing. `--apply` writes `ado.stack.json` atomically (a temp file in the same directory, then a rename, then a read-back; an existing
file is kept when the write fails; a failed read-back after the rename is reported) and removes `.ado/stack-draft.json` when it was the source: the validated copy is the profile, not the
agent's file. Replacing a profile that is `approved`, `changed-since-approval`, or hand-broken but still marked approved needs `--force` (a symlinked `ado.stack.json` is always refused), and `--force` over an approved profile needs the human's explicit yes: the agent passes the flag itself, so it must ask first. Exit 0 for a preview or a write, 2 for a
refusal or an invalid profile. Seed `python-pytest` is researched: 12 official sources (pytest, Python, packaging.python.org, ruff, pytest-cov, pip-audit, PEP 8), every URL re-fetched on 2026-10-31, status `draft` (a human approves it per project). Its test command is `PYTHONPATH=src python3 -m pytest -q`; set `strict.test_timeout` >= 900, because each run has an empty temporary `HOME`. The package and its test tools must already be installed in the active environment (the run installs nothing). Seed `node-typescript` is researched too: 10 official sources (docs.npmjs.com, typescriptlang.org, nodejs.org, eslint.org), every URL re-fetched on 2026-10-31, status `draft`, npm only (pnpm and yarn go through research). Its test command is `npm ci --ignore-scripts && npm test`; set `strict.test_timeout` >= 900, because the npm cache starts empty and every run needs the network. Trade-off: `--ignore-scripts` also skips the project's OWN `package.json` lifecycle scripts (for example `prepare` and `postinstall`), so a TypeScript project that compiles in `prepare` will fail or run stale code and must set its own test command; dependency install scripts do not run either, so a package that needs a postinstall build step may fail (the npm docs say `--ignore-scripts` outranks allowing dependency install scripts; that every dependency script is skipped is a local observation, marked unverified in the seed). The `--audit-level=high` level is an unverified design choice. It ships `npm audit --audit-level=high` (which sends a description of the dependencies to the registry), no lint or type-check command (no authoritative generic one), and needs the project's own test dependencies in `package-lock.json`. Seed `salesforce` is researched too: 13 sources (help.salesforce.com, github.com/salesforce, github.com/salesforcecli and github.com/forcedotcom, docs.npmjs.com, the Code Analyzer GitHub Action page, and two files of the dreamhouse-lwc sample app as `implementation` sources), every URL re-fetched on 2026-10-31; developer.salesforce.com refused automated fetches (HTTP 403), so none of its pages is cited, and a claim only that site could support is marked `unverified`. Status `draft`. Its test command is `npm ci --ignore-scripts && npm run test:unit` (LWC Jest only); set `strict.test_timeout` >= 900, because the npm cache starts empty and every run needs the network. Trade-off: `--ignore-scripts` also skips the project's OWN lifecycle scripts, so a repo that installs git hooks from `prepare` (the sample app runs husky there) gets none in the strict clone, and a project that builds in `postinstall` must set its own test command; dependency install scripts are skipped too (unverified on every npm version). Apex tests and deploys need a live org, so `sf apex run test ...` and `sf project deploy start --dry-run ...` sit in `org_only`: `strict verify` never runs them, and the agent shows one and runs it only after the human's explicit yes for that run. Strict L2 therefore covers LWC Jest, not Apex. The seed also ships `coverage` (`npm run test:unit:coverage`, LWC only) and Salesforce Code Analyzer as `security` (`sf code-analyzer run --severity-threshold 2`; the plugin must already be installed, and the flag, the level and any run-time download are unverified). It ships no `lint` or `format_check` command (no general one exists); one convention, from a single sample app and unverified for other projects, says to run the project's own `lint` and `prettier:verify` scripts if it defines them. It does not set `kit`. All three shipped seeds are researched drafts; the next step after any init is the human's review and approval. This is process enforcement, not a security boundary.

## Seed profiles

Three researched seeds ship as package data in `src/ado/seeds/` (`ado stack init --seed <name>`). All are `draft`: nothing is trusted
until a human approves the profile for the project. Every source URL was re-fetched on the retrieval date below; the dates, counts and
kinds are read from the seed files.

| Seed | Stack id | Sources (kinds) | Retrieved | `unverified` in the approval count | Other claims marked unverified only in a `why` or in the text |
|---|---|---|---|---|---|
| `python-pytest` | `python` | 12 (12 official) | 2026-10-31 | 0 | `commands.test.why`: the `-q` flag, and that dependencies and test tools must already be installed |
| `node-typescript` | `typescript` | 10 (10 official) | 2026-10-31 | 0 | `commands.test.why`: that every dependency script is skipped (seen once); `commands.security[0].why`: the `high` audit level |
| `salesforce` | `salesforce` | 13 (11 official, 2 implementation) | 2026-10-31 | 1 (`conventions[10]`) | `commands.test.why`; `commands.security[0].why` (the flag, the level, run-time fetches); `commands.org_only[0].why` and `[1].why` (the wait, the format, dry runs on production); `conventions[1].rule`; `dod[4].item` |

**The `unverified` count is a lower bound.** The approval preview counts only items whose source is the literal `unverified`. A claim a
seed marks as unverified inside a `why` is not counted, and the preview prints a `why` only for a command whose `run` is `null`; for a
command that has a `run` the `why` is in the file only. So before you approve a profile, open `ado.stack.json` and read every `why`
(and the `rule` and `item` texts, which the preview does print) for the words "unverified" and "design choice". The counts of 0 and 1 in
the table hide the open claims in each seed. This is a limit of the preview, documented rather than fixed in this release.

The seeds in one line each: `python-pytest` runs `PYTHONPATH=src python3 -m pytest -q`, with ruff, pytest-cov and pip-audit commands;
`node-typescript` runs `npm ci --ignore-scripts && npm test` (npm only) with `npm audit --audit-level=high`; `salesforce` runs the LWC Jest
tests (`npm ci --ignore-scripts && npm run test:unit`), keeps Apex tests and deploy validation in `org_only`, and sets no `kit`. The
details, trade-offs and `strict.test_timeout` advice are under "Creating a profile" above. Flutter, Java, Go, Rust, .NET, C/C++, Pega
and Appian have no seed: `speckit.ado.stack` researches a cited draft, `unverified` where it cannot cite. This is process enforcement,
not a security boundary.

## Approving a profile

`ado stack approve` is the human's step: it records the approval and is the only place `strict.test_command` is written once a profile
exists. It changes what strict CI runs, so the agent never runs it: after `ado stack init --apply` it tells the human to run
`ado stack approve` and then `ado stack approve --apply` in their own terminal window (not the `!` prefix: it is not verified to give a tty).

- **Preview** (no `--apply`). When stdin and stdout are both terminals it prints the current status (draft, a re-approval of version N, or changed since version N; a warning when `ADO_CONFIG` is set, since the current value shown then comes from that file and `--apply` refuses), then, per stack, every command verbatim and in full in a code block (`org_only` ones
  flagged: the agent runs one only after your explicit yes for that run, and `strict verify` never does), each rule with its source kind
  and URL, the count of `UNVERIFIED` items, and the exact `strict.test_command` it will write, or "left unset: no stack defines a test
  command". With several stacks the command is `( cd ./<root> && sh -c '<run, shell-quoted>' )` joined by ` && `, so read the whole line.
  Control characters and escape sequences are stripped before printing, and the text is printed, never stored in a file or log. Without a
  terminal on stdin, or with the output piped or redirected, it prints counts, the unverified count and the command's length only: the profile's text came from untrusted
  research and is not handed to an agent's shell. The command never fetches a URL, and nobody checked that a page supports a note: you are the check.
  **The `UNVERIFIED` count is a lower bound.** It counts only items whose source is the literal `unverified`. A claim marked unverified
  inside a command's `why` is not counted, and the preview prints a `why` only for a command whose `run` is `null`. Open `ado.stack.json`
  and read every `why` before you approve (the shipped seeds each have such claims: see "Seed profiles").
- **`--apply`** refuses (exit 2, fixed text) unless stdin is a terminal, before it reads any file or the config, and refuses when
  `ADO_CONFIG` is set. It sets `status: approved`, raises `version` by 1 and writes `approval {by, date, sha256, decision}`: `--by` is a
  role (letters, digits and spaces with at least one letter or digit, at most 40, default `tech lead`; the validator also refuses any other `approval.by`), `sha256` is the
  hash of the profile as written, `decision` is `US<id> D<n>` when `--story` is given. A draft, a changed or an already approved profile can
  be approved; each approval is a new version.
- **The config.** `strict.test_command` becomes the composed command, or is removed when no stack defines `test.run`; a composed command
  over 500 characters is refused before anything is written (point `test` at a repo script, kind `repo`). The config is parsed and
  written back with the parsed values and key order of every other key unchanged and its indent, line terminator and trailing newline
  kept (spacing may change). The new text is read back and checked by the config validator before it replaces the file (atomic, same
  mode). A missing, invalid, duplicate-key, symlinked or non-regular config is refused and left as it was.
- **`--story ID`** (digits) adds one decision line, `Stack profile v<n> approved, sha256 <12 hex>`, to `<dod_path>/Decisions_<id>.md`,
  so the normal decisions post carries it to the PO. The decision, config and profile are written in that order, each checked before it
  replaces the file, and a failed step puts the earlier ones back.

The terminal check stops accidents; it is not a security boundary (a pseudo-terminal gives a tty, and anyone can edit the files), and the
approval is a record, not proof: trust comes from reviewing the commit, and in strict CI from the target branch's copy. This is process
enforcement, not a security boundary.

## Definition-of-done items

When `ado dod <id>` creates a **fresh** ledger (first run or `--force`) and `ado.stack.json` is **approved**, each stack's `dod` items are
appended to section 5 after the five fixed lines as unticked boxes, `- [ ] <item> (stack <id>, S<n>)` (`unverified` in place of `S<n>` for an
unsourced item). They are plain checkbox lines, so `ado done` and the strict `done` check count them: a stack item left unticked means the
story is not ready. Tick one only with evidence. A draft, invalid or changed-since-approval profile, or none, adds nothing, and the ledger
is byte-identical to one made without this feature. `ado dod --refresh` never touches section 5, so approving a profile later or editing its
items does not change an existing ledger (add a line by hand, or use `--force` on a ledger you have not yet worked on). Item text is
cleaned like the rest of the profile text (control, line-separator and invisible characters, `<!--`, `-->` and code fences removed, 300
characters) and at most 20 items are seeded; more adds one line `N more stack DoD items were not seeded: run ado stack show --md`.
This is process enforcement, not a security boundary.

## Strict rules and CI

In strict mode `ado strict check` runs two stack rules in the `implement`, `pr` and `done` phases. Neither can be overridden (they are not
in the override table): an unknown or unapproved stack stops the run.

- **`stack-profile-*`.** `stack-profile-missing` (no `ado.stack.json`), `stack-profile-invalid` (fails the checks above; the finding
  carries a count, not the errors: run `ado stack show`), `stack-profile-draft`, `stack-profile-changed` (the hash no longer matches) and
  `stack-profile-drift`: the approved profile composes a test command and `strict.test_command` differs (or the composed command is over
  500 characters). A profile whose `test` is `null` everywhere compares nothing, so no drift fires; the `test-command` finding is then
  covered only by a per-story `l2` (or `test-command`) override, which is the recorded waiver.
- **`stack-unprofiled`.** `ado stack detect` runs on the commit being judged (the PR head in CI) and every high-confidence root must be the
  `root` of a profile stack. A stack with a free-form id covers its root; one with a detect id covers only its own family (`node` and
  `typescript`, `dart` and `flutter` are one each: the `node-typescript` seed has id `typescript` while detection may say `node`), so a
  Python profile at `.` does not cover a TypeScript project added at `.`; a medium-confidence root is not a finding; roots are printed through the plain-path rule (`<path #n>`
  otherwise). When detection cannot run the finding says so (fail closed).

**Where the profile comes from.** A local strict config reads the working-tree `ado.stack.json`. In CI (`--base`, a strict config on the
target branch) the profile, `ado.kits.lock.json` and `strict.test_command` all come from the **target branch's commit**: `_config` reads
both files with the same capped blob read as the config (256 KiB each) and fails closed on an unreadable or invalid one (exit 3 for `gate`,
`branch --id-from`, `strict mode`; 2 for `strict check`), with fixed texts that carry none of the file (the gate comment says "target branch
trust files are invalid", not that the config is). So editing the profile, the lockfile or `strict.test_command` does not change the
command or the verdict **in that PR's own run**. The PR's copies are judged as well, because they become the target's on merge: when the
head's profile or `strict.test_command` differs from the target's, `strict check` also refuses a head profile that is invalid, unreadable,
a draft, changed since approval, deleted, or that does not compose to the head's `strict.test_command` (a null `test` with no command is
consistent), with the same `stack-profile-*` ids and the detail "at the head commit". These findings only add to the target's verdict.
`ado strict verify` runs only the ledger, L1 and test-command rules and applies no stack or kit rule. Protect the files on the target
branch like `ado.config.json`.

**The note.** A PR that edits the profile, the lockfile or `strict.test_command` (or adds the first profile) gets
`"warnings": ["trust-introducing PR: review ado.stack.json as code"]` from `strict check`, and the `gate` PR comment carries the same line with the JSON key `trust_note`. The note
fires on every such edit, consistent or not: once merged the edit is in force with no further note (an approved hash is computable by
anyone), so review it as code. What runs in CI is the merged `strict.test_command`, which `ado stack approve` sets from the profile.

**Bootstrap.** When the target branch has no `ado.stack.json`, the stack rules give no finding against a target. The PR that adds the
first profile must still be sound: its head profile has to be valid, approved and consistent with the head's `strict.test_command`
(an invalid, draft or changed one is refused, so merging it cannot lock the repository). Once the target holds a profile the rules bind,
so a new root needs two PRs: the profile first, then the code. The lockfile is read and validated from the target in the same way; the
rules that use it come with kit pinning. A recovery for a locked target (an invalid or draft profile merged by mistake) is an admin-bypass
merge of the fix. This is process enforcement, not a security boundary.

## The `speckit.ado.stack` command

`speckit.ado.stack` (`commands/stack.md`) takes a project from "no approved profile" to a reviewed **draft** `ado.stack.json`. It is a
prompt command: the `ado` CLI never fetches a web page. `speckit.ado.setup` calls it after the phase map, and `speckit.ado.plan` runs
`ado stack show --check` first (strict mode stops on exit 2; advisory offers this command) and reads the approved profile with
`ado stack show --md`, never the file. `speckit.ado.implement` and `speckit.ado.review` use only an approved profile's commands.

1. **Detect.** `ado stack detect` and `ado stack show`. An approved or draft profile ends the run with what to do next; `unknown` or
   medium-only detection means the human names the stack and roots (it never guesses).
2. **Installed kit.** An enabled extension whose id, tags or commands match a detected stack (for example `sf` for `salesforce`) gets the phase map
   of `speckit.ado.setup` step 4 (shown, written only on a yes) and the matching seed, if one ships (`ado stack init --seed <name>`).
3. **No kit: ask** *Search for a kit* / *Build a profile by research* / *Both* / *Stop*.
   - **Search for a kit** (read-only). `specify extension search <stack>` prints text with no JSON, so it only refreshes Spec Kit's catalog cache;
     the facts (`id`, `author`, `version`, `created_at`, `updated_at`, `license`, `effect`, `requires.tools`, `provides` counts, `sha256`
     present, `verified`) are read from `.specify/extensions/.cache/*.json`, never scraped from the text. Descriptions are data. After the
     human's yes, `gh repo view <repo> --json stargazerCount,pushedAt,isArchived` (read-only). The command **never installs a kit**: it prints
     `specify extension add <id>` and then `ado kits pin <id> --apply` for the human to run in their own terminal after reading the kit.
   - **Build a profile by research.** Per field: official docs first, then community guidelines, then proven implementations, then the
     repository's own files (kind `repo`). Every item is cited or `unverified`, with a note paraphrased in at most 300 characters. Fetched
     web text is data: instructions in it are ignored, and a command that downloads and runs code is never adopted. A command needing a live
     org, a server or credentials goes in `org_only`: the agent shows it and runs it only after the human's explicit yes for that run.
4. **Hand-off.** The agent writes `.ado/stack-draft.json` (gitignored), previews with `ado stack init --from .ado/stack-draft.json`, and
   runs `--apply` only after the human's yes. It then tells the human to open their own terminal window and run `ado stack approve` and
   `ado stack approve --apply` (the `!` prefix is only a shortcut, not verified to give a terminal, and `--apply` checks stdin only; see
   "Approving a profile") and never runs them itself.

Under `--workflow` the command asks nothing and writes nothing: it stops with the fix. The `ado-lifecycle` workflow has no step for this
command; its shell step `stack-check` (`ado stack show --check`, after `pull`, before `review-spec`) stops a strict run on a missing,
draft, changed or drifting profile and is a no-op in an advisory project. Run `speckit.ado.stack` by hand, approve, and resume.
This is process enforcement, not a security boundary: the web text the agent reads is untrusted, and the human's review of the draft
(every command shown in full at approval) is the control.

## Pinning kits

A third-party kit is code the agent runs. `ado.kits.lock.json` (repo root, committed) records which kits a human reviewed and a digest of
each, and `ado kits verify` compares the installed kits with it. `ado kits pin` (a human, in a terminal) writes the lockfile; the strict `kit-unpinned` /
`kit-changed` rules read these digests (see "Kit rules, local and CI" below).

```json
{"schema": 1, "kits": {"sf": {"version": "1.0.0", "source": "https://example.com/contoso/sf", "catalog_sha256": null,
                              "dir_digest": "<64 hex>", "agents_sha256": "<64 hex>"}}}
```

- **`dir_digest`:** sha256 over the sorted lines `<git blob id>  <path inside the kit dir>` of the regular files git tracks or would
  track (`git ls-files --cached --others --exclude-standard`) under `.specify/extensions/<id>/`, except `local-config.yml`,
  `*-config.local.yml` and the kit's own `<id>-config.yml` at the top of that dir (settings you edit, not vetted). Blob ids come from
  `git hash-object`, so the value agrees with the committed one under CRLF and `core.autocrlf`; they are SHA-1 in SHA-1 repositories.
  Spec Kit's own `manifest_hash` covers only `extension.yml`, which is why it is not used.
- **`agents_sha256`:** sha256 over the bytes of the Claude copies Spec Kit registered, found by the **exact** names in the `.registry`
  entry (`registered_commands.claude`, `registered_skills`; a command `speckit.sf.specify` is the skill
  `.claude/skills/speckit-sf-specify/SKILL.md`, else `.claude/commands/speckit.sf.specify.md`), never a glob, so `sf` and `sf-extra` do not
  mix. Those copies are what the agent actually runs. Only the Claude layout is covered.
- **Refused, not skipped:** an empty kit dir, a path with a control character, a kit path to which `.gitattributes` (or any attributes
  source) gives `ident`, `filter` or `working-tree-encoding` (`git hash-object` would rewrite the file or run a filter driver, so the local
  value could differ from the committed one; `text` and `eol` are fine; the check runs before anything is hashed), a symlink or other non-regular file, a file over 16 MiB,
  a registry entry that is a symlink, corrupt, over 1 MiB or has a duplicate key. A kit installed with `specify extension add --dev`
  has symlinked agent copies, so it **cannot be pinned**: reinstall without `--dev`. An `.registry` entry without `enabled` counts as
  enabled (as upstream); an id that is not `[a-z0-9-]{1,40}` is reported as `unpinned` (`id: null`), never skipped; `ado` itself is not a
  kit.
- **The lockfile is read as hostile too:** at most 256 KiB, no duplicate key, schema 1, kit ids and 64-hex digests checked; `source`
  must pass the profile's https URL rules and `version` be `[0-9A-Za-z.+-]{1,40}`, else it is read as `null`.

`ado kits verify` (local, read-only, no token, no network) prints `{"kits": [{"id", "state"}], "findings": [...]}` for every kit in the registry, **disabled ones included** (`specify extension disable` only flips `enabled`; the agent copies stay on disk and invocable):
`pinned`, `unpinned` (not in the lock), `changed` (a digest or the version differs, or a file could not be read; `reason` is one of
`symlink`, `unreadable`, `empty`, `control-char`, `attributes`) or `stale` (locked but no longer in the registry; a pinned kit whose registry entry is removed while the registry file exists and the kit's files remain on disk is reported `stale` with `reason: "files-remain"` and a `kit-changed` finding, locally and in strict mode; with no registry file it is only `stale`). Exit 2 on any `unpinned` or `changed`
kit (or a `stale` one whose files remain), or when the registry cannot be trusted. Output carries kit ids (which match `[a-z0-9-]{1,40}`) and fixed words only, never kit prose.
`kits.dir_digest_at(repo, commit, id)` computes the same digest from the files of a commit (what CI will use; a symlink or submodule entry
gives the value `"changed"`). This is process enforcement, not a security boundary: a digest says a kit is unchanged since someone
looked, not that it is safe, and anyone who can edit both the kit and the lockfile can re-pin.

**Scan facts.** `kits.scan(root, id)` reads `extension.yml` with a line scanner over fixed keys (no YAML library) and the command and
script files it names, with confined, size-capped, no-symlink reads, and returns `{"commands", "hooks", "scripts", "tools", "flags"}`:
the number of commands and scripts, hook events as `<event> (optional)` or `<event>`, `requires.tools` names, and for five flags
(`network`, `installs`, `writes`, `secrets`, `allowed_tools`) a count plus `<relpath>:<line>` locations (paths relative to the kit
dir, at most 50 listed). Hook names are shown only if they match `[a-z_]{1,40}` and tool names only if they match `[A-Za-z0-9._-]{1,40}`,
else as `<other>`; no kit prose is ever printed (the strings that do come from the kit are hook names, tool names and kit-relative
paths, each limited to a fixed pattern, so they cannot hold a space, a newline or an escape character, but they are kit-chosen and not fixed text). A manifest, command or script file that is missing, a symlink, oversized or named by an
unsafe path sets `"unreadable": true` (the scan is then incomplete, not clean). **The scan is a heuristic aid for the human reviewer, not
a security verdict:** fixed text patterns miss obfuscation and a block of YAML it does not parse, so a count of 0 proves nothing, and the
things only a person can judge (prompt text that steers the agent, scripts that do more than their names say, the maintainer, licence)
stay with the reviewer.

**Pinning: `ado kits pin <id> [--by ROLE] [--apply]`.** `<id>` must be an installed kit (a registry entry; anything else, including `ado`, an id
that does not match `[a-z0-9-]{1,40}` or a kit that is not installed, exits 2 with the fixed text `error: kits pin refused: unknown` and the
id is never echoed; an incomplete scan, that is `"unreadable": true`, refuses too, as `error: kits pin refused: incomplete-scan`: fix or reinstall the kit first). The **preview** (the default) writes nothing and prints JSON: `applied: false`, the `id`, the registry `version`, the
two digests, `source_recorded` (whether the manifest's `extension.repository` is an acceptable https URL; the URL is kit text, so it is
recorded in the lock and not printed), the **scan facts** of `kits.scan` (counts, `<relpath>:<line>` locations, hook and tool names only when
they match their patterns) and `human_checklist`, five fixed lines (read every command and script; open each flagged file:line; judge the
maintainer; check licence and process fit; and that the pin is only your approval record, `<id>-config.yml` and local config files are not
vetted, and a `--dev` install cannot be pinned). The preview prints no prose from the kit. What it does show is kit-chosen but pattern-limited: hook names (`[a-z_]{1,40}`), tool names
(`[A-Za-z0-9._-]{1,40}`) and kit-relative paths (at most 200 characters of `[A-Za-z0-9._/-]`, else `<path #n>`); none can hold an escape
character, a space or a newline, but they are not fixed text. Reading the kit is the human's job, not the checklist's. When the kit is already pinned the preview adds a fixed `note` that `--apply` **replaces** that entry. **`--apply` refuses (exit 2, fixed text) unless stdin AND stdout are both
terminals**, tested before any file is read, so the human has the full output in front of them; the check stops accidents and is not a
security boundary (a pseudo-terminal gives a tty). Preview and `--apply` are separate runs and nothing binds them (as for `stack approve`): `--apply` recomputes everything and pins the kit **as it is now**, so compare the digests in its output with the ones you saw in the preview. It writes
the entry `{version, source, catalog_sha256, dir_digest, agents_sha256, reviewed: {by, date}, scan: {commands, hooks, scripts, tools,
flags: {network, installs, writes, secrets, allowed_tools}}}` (`catalog_sha256` is always `null` for now: Spec Kit's catalog cache is not read). `--by` is a role (letters, digits, spaces, at most
40; never a name; default `tech lead`), validated exactly as `ado stack approve --by`. The write is atomic (a temp file in the repository
root and `os.replace`, keys sorted, the file's line terminator kept, read back and checked before the rename); an existing lockfile is
re-validated first (a symlink, a non-regular file or invalid content refuses) and every other kit's entry is kept as it was; on any
failure the existing lockfile is left byte-identical. Pinning an already pinned kit replaces its entry: upgrading a kit means reading the
diff and pinning again.

Limits, stated plainly: **`--dev` installs whose registered agent copies are symlinks cannot be pinned** (usually the case: the pin refuses with `symlink`;
reinstall without `--dev`; a dev install with no registered copies is not caught by this check), as are a symlinked kit dir, an empty kit dir and a kit with no `.registry` entry. **`<id>-config.yml` is not
vetted** (it is excluded from `dir_digest`, so a project setting can change without a re-pin). The pin is a **human approval record,
process enforcement, not a sandbox**: it says someone looked at these bytes, it does not run the kit in isolation, and anyone who can edit
the lockfile can re-pin.

## Kit rules, local and CI

In strict mode `ado strict check` runs two kit rules in the `implement`, `pr` and `done` phases, after the stack rules. Neither can be overridden
(they are not in the override table), and each fires at most once per run, naming kit ids and counts (a kit id matches `[a-z0-9-]{1,40}`; an
entry that has no usable id is `#<n>`) and never kit, lockfile or path text.

- **`kit-unpinned`:** an installed kit is not in `ado.kits.lock.json`.
- **`kit-changed`:** a pinned kit's `dir_digest`, `agents_sha256` or version differs from the pin, a file could not be read safely (a symlink, an
  empty or odd kit dir, a symlinked `<id>-config.yml`), a pinned kit's registry entry is gone from an existing registry while its files remain on disk, or the registry, a kit file or the lockfile cannot be
  trusted.

The fix is one terminal command the **human** runs after reading the kit: `ado kits pin <id> --apply` (`ado kits pin <id>` previews it). Upgrading a
kit means pinning it again.

**Locally** (a strict config read from the working tree) the rules are `ado kits verify`: the registry, the kit dirs (tracked or not yet tracked
files) and the Claude agent copies, **disabled kits included**, against the working-tree lockfile. A strict project with no kit installed gains
nothing and runs no git for this.

**In CI** (`--base`, a strict config on the target branch) the lockfile is the **target branch's**, read once with the config and profile, and
the files judged are the ones git **tracks** at the PR head:

| CI checks | CI does not check (local only) |
|---|---|
| each kit in the target lockfile: `kits.dir_digest_at(repo, head, id)` of its tracked files against the pinned `dir_digest` | `.specify/extensions/.registry`, so enabled/disabled and the installed version |
| a newly tracked `.specify/extensions/<dir>/extension.yml` whose `<dir>` is not in the target lockfile: `kit-unpinned` (a `<dir>` that is no valid id is `#<n>`; a symlink or submodule where an unpinned kit dir would be is also unpinned) | agent copies (`.claude/...`) and `agents_sha256`: a PR that edits only an agent copy passes CI and is caught by `ado kits verify` on a developer's machine |
| a symlink or submodule entry inside a kit dir (an excluded `<id>-config.yml` too), in place of a kit dir, or in place of `.specify` or `.specify/extensions`: `kit-changed`. A tracked dir below `.specify/extensions/` whose name is no kit id (`SF`, a fullwidth or Cyrillic lookalike), with or without a manifest, is `kit-unpinned` (`#<n>`), and a top-level `.Specify` or `.specify/Extensions` (case or Unicode variants) is `kit-changed`: on a case-insensitive checkout they land in the kit dirs. Names are never echoed | untracked files, ignored files and the contents of `<id>-config.yml` / local config files |
| the PR's own `ado.kits.lock.json` is judged for being sound and present (see below), never used to judge | a kit installed on a developer's machine and never committed |

A locked kit with **no tracked files** at the head is a warning, `kit not in the repository: checked locally only (<ids>)`, not a finding (a
repository that commits no kit dirs is legitimate); that includes a PR that deletes the whole kit dir, so review a PR that removes tracked kit
files. The kit rules are in the same `warnings` list as the other notes.

**A PR cannot change what CI accepts by editing the lockfile.** The target's lockfile judges that PR's run, so re-pinning a tampered kit in the
same PR still gives `kit-changed`, and adding a kit and its pin together still gives `kit-unpinned`: a new kit takes two PRs, the pin first (its
digest is computed on a developer's machine), then the kit. Because the PR's copy becomes the target's when it merges, `strict check` also refuses
a PR that **deletes** `ado.kits.lock.json` (merged, the rules would turn into a bootstrap note) or whose lockfile is unreadable or not a lockfile
this version can read (`kit-changed`, detail "at the head commit"). Any later PR that edits the lockfile carries the stack note
(`trust-introducing PR: review ado.stack.json as code`); the PR that adds the first one carries only its own (below): review lockfile edits as code.

**Bootstrap.** When the target branch has no `ado.kits.lock.json`, CI gives no kit finding (not for a tracked kit either). The PR that adds the
first lockfile carries `trust-introducing PR: review ado.kits.lock.json as code` (`warnings` of `strict check`; the `gate` PR comment and the JSON
key `kits_trust_note`), and its lockfile must be sound (an unreadable one is `kit-changed`, so merging it cannot lock the repository). Once the
target holds a lockfile the rules bind. A local strict config always binds. **Advisory mode** only reports: `strict check` returns
`mode is advisory` as before and `ado kits verify` prints the same facts. `ado strict verify` applies no kit rule.

**Limits, plainly.** CI reads git objects, not a running agent: it cannot see which kit commands the agent actually executes, nor an agent copy.
A lockfile entry on the target that cannot be read as a pin (for example a hand-written `{"version": "1.0.0"}`) is `kit-changed` for every
PR until it is fixed on the target (an admin-bypass merge). Upstream's guide says not to commit `.specify/extensions/.registry`, and link-on-merge `ado done` judges a fresh checkout with the local rules. So
the "registry entry gone, files remain" finding fires **only when the registry file exists**: with no registry file a pinned kit is reported `stale`
with no finding (`ado kits verify` exits 0), and the local check has nothing to read: it cannot see an unpinned kit, nor an edit to an agent copy.
Agent copies are checked on a machine that has the registry and the copies (a developer's); CI sees only the tracked kit dirs. With more than 5 kits the finding lists five ids and the count. Process
enforcement, not a security boundary: a digest says a kit is unchanged since a human looked, and anyone who can edit the target branch's
kits and lockfile can re-pin.

## Adopting profiles and kits: the two-PR flow

In a strict project the target branch decides what CI accepts, so a new stack, profile or kit goes in two steps. Do not combine them.

1. **The trust PR.** Add `ado.stack.json` (a human ran `ado stack approve --apply`, which also set `strict.test_command`) and, for each
   kit, `ado.kits.lock.json` (a human ran `ado kits pin <id> --apply`). When the target branch has no profile or lockfile yet, CI cannot
   judge the PR against one, so the stack and kit rules are notes for this PR and it carries the flagged note
   `trust-introducing PR: review ado.stack.json as code` (for the first lockfile, `trust-introducing PR: review ado.kits.lock.json as code`). **The note
   means the reviewer reads the file as code**: what runs in CI after the merge is the command in the profile. The head files must still be
   sound (valid, approved, consistent with `strict.test_command`; a lockfile that can be read), so merging cannot lock the repository.
2. **Feature PRs.** Once the profile and lockfile are on the target branch the rules bind: a new high-confidence root is
   `stack-unprofiled`, an unpinned kit is `kit-unpinned`, a changed one `kit-changed`, and a PR that edits the profile, the lockfile or
   `strict.test_command` does not change its own verdict (the target's copies judge it) and carries the note again. A new root or a
   new kit therefore takes two PRs: the profile or lock first, then the code or kit.

An admin-bypass merge is the recovery for a bad profile or lockfile already on the target. Protect both files with branch rules or
`CODEOWNERS` like `ado.config.json`. This is process enforcement, not a security boundary: whoever can merge to the target branch can change what runs.

## Kits that do not write `tasks.md`

`ado push` reads one format: the Spec Kit `tasks.md` checklist (`- [ ] T001 [P] ...`). If
the mapped tasks command writes something else (for example one file per story),
`speckit.ado.tasks` writes a standard `tasks.md` from that output, one line per item,
citing the source file. You check it at the push preview before anything reaches the
board.
