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
refusal or an invalid profile. The shipped seeds are PLACEHOLDERS (valid, unresearched drafts for tests) until the seed tasks P2-11 to P2-13 replace them with cited,
re-fetched ones; the next step after any init is the human's review and approval. This is process enforcement, not a security boundary.

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

## Kits that do not write `tasks.md`

`ado push` reads one format: the Spec Kit `tasks.md` checklist (`- [ ] T001 [P] ...`). If
the mapped tasks command writes something else (for example one file per story),
`speckit.ado.tasks` writes a standard `tasks.md` from that output, one line per item,
citing the source file. You check it at the push preview before anything reaches the
board.
