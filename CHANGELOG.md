# Changelog

All notable changes are listed here. Versions follow [VERSIONING.md](https://github.com/praveenlavu/spec-kit-ado/blob/main/VERSIONING.md).

## 0.3.0 — unreleased

### Added

- `ado stack detect [--repo DIR]`: read-only, local detection of the stacks a repository holds, from the files tracked at `HEAD`
  (`git ls-tree -r -z`; never the working tree) as JSON `{"outcome": "detected|multiple|unknown", "stacks": [{"id", "root", "confidence",
  "evidence", "hints"}], "domain_hints": []}`. Ids: `python`, `node`, `typescript`, `dart`, `flutter`, `salesforce`, `java`, `go`, `rust`,
  `dotnet`, `c-cpp`; fixed marker names at most 3 segments deep; a medium marker alone is listed, never `detected`; `sfdx-project.json`
  absorbs the `package.json` beside it, `pubspec.yaml` absorbs `android/` Gradle projects, `typescript` replaces `node`; two roots are
  `multiple`. Hints come from a few manifests read from the commit's blobs (regular files of at most 1 MiB, never a symlink); `media`
  and `pega` are domain hints, and Pega and Appian projects are otherwise `unknown`. Paths are untrusted: printed only when they match
  `A-Za-z0-9._/-` (1 to 200 characters), else as `<path #n>`. Nested roots of the same stack (npm workspaces, Maven/Gradle modules, Cargo workspaces) fold into their nearest ancestor root as evidence (one stack); roots of different stacks stay separate. `note: "hints incomplete"` when a manifest could not be read as asked (read budget of 32, a `package.json` that is not a JSON object or repeats a key); SHA-1 repositories only. No commit is `unknown` with `note: "commit first"`; git failures are exit 2
  with fixed texts; no verb acts on it yet and advisory behaviour is unchanged. See `docs/stack-kits.md` "Detection".
- `ado dod` seeds the `dod` items of an approved `ado.stack.json` into section 5 of a fresh ledger (first run or `--force`) as unticked
  `- [ ] <item> (stack <id>, S<n>)` lines after the five fixed ones: cleaned, escaped, at most 20 (a fixed overflow line beyond that). `--refresh`
  never touches section 5; no approved profile, no change (byte-identical ledger). `ado done` counts them. See `docs/stack-kits.md` "Definition-of-done items".
- `ado stack show [--json|--md] [--check]`: read-only, local view of the stack profile `ado.stack.json`. `stack.read_profile` (a confined
  working-tree read, or bytes of a git blob), `stack.load_profile` (the profile only when approved), `stack.profile_hash` (sha256 of the
  profile without `approval`, sorted keys), `stack.compose_test_command` (one stack at `.`: its `run`; otherwise
  `( cd ./<root> && sh -c '<quoted run>' )` joined by ` && `, null tests skipped, over 500 characters refused) and `stack.render_md`.
  Status is `none`, `invalid`, `draft`, `approved` or `changed-since-approval`. The text of a profile that is not approved is never
  printed (status, counts and fixed error texts only); an approved one is printed by `--md` as data with control characters, `<!--`, `-->` and
  code fences removed. `--check` exits 2 in strict mode unless the profile is approved, matches `strict.test_command` and covers every
  high-confidence root; in advisory mode drift and unprofiled roots are `notes` and the exit is 0. Commands in a profile are shell code that
  a human approves; the validator is a tripwire, not a sandbox. Nothing else acts on the profile yet; advisory behaviour is unchanged.
  See `docs/stack-kits.md` "The stack profile".
- `ado stack init (--from FILE | --seed NAME [--root R]) [--stack ID] [--apply] [--force]`: creates the draft profile `ado.stack.json` from a
  seed (package data `src/ado/seeds/*.json`: `python-pytest`, `node-typescript`, `salesforce`; the name is matched against `stack.SEEDS`
  only, never joined into a path; `stack.seed_for` is a fixed table) or from a file under `.ado/` (a regular, non-symlink file). The
  profile is validated, `status` is forced to `draft` and `approval` dropped; a seed's root becomes `--root` or the detected root, then
  is validated again. A local write (no ADO call, no gate: the draft is not trusted until approved): a preview by default that prints
  `{ok, errors, counts}` only, `--apply` writes atomically with a read-back, an approved or changed profile (or one that still carries an approval) needs `--force`, and a symlinked `ado.stack.json` is refused.
  `stack.plan_init` and `stack.apply_init`. The seeds shipped so far are placeholders for tests; the researched seeds replace them.
  See `docs/stack-kits.md` "Creating a profile".
- `ado stack approve [--story ID] [--by ROLE] [--apply]`: a human approves the profile and sets what strict CI runs. `--apply` refuses
  (exit 2) unless stdin is a terminal, before any file or the config is read, and when `ADO_CONFIG` is set; a terminal check stops
  accidents, it is not a security boundary. The preview prints every command verbatim when stdin and stdout are both terminals (stripped of control characters, never
  stored, with the current status and a warning when `ADO_CONFIG` is set) and counts only otherwise; a Ctrl-C between writes puts every earlier write back. `--apply` sets `status: approved`, raises `version`, writes `approval {by, date, sha256, decision}` and
  sets or removes `strict.test_command` in `ado.config.json` (parsed values and key order of every other key unchanged, indent, line
  terminator and trailing newline kept, atomic, read back; refused for an invalid, symlinked or non-regular config or a composed command
  over 500 characters); `--story` adds one decision line; a failed step puts earlier writes back. `stack.validate` now limits
  `approval.by` to a role (`[A-Za-z0-9 ]{1,40}`). Once a profile exists, `strict.test_command` comes only from this verb; `ado setup` never
  writes it. `stack.plan_approve`, `stack.apply_approve`, `stack.preview_text`. See `docs/stack-kits.md` "Approving a profile".
- Strict stack rules (`strict check`, phases `implement`, `pr`, `done`; not overridable): `stack-profile-missing`, `stack-profile-invalid`,
  `stack-profile-draft`, `stack-profile-changed`, `stack-profile-drift` (only when the approved profile composes a test command and
  `strict.test_command` differs) and `stack-unprofiled` (a high-confidence `ado stack detect` root at the head commit that no profile stack
  covers, by root and id family: `node`/`typescript`, `dart`/`flutter`; a free-form stack id covers its root). A strict project now needs an
  approved `ado.stack.json`; an advisory one changes nothing (`strict check` output is byte-identical). In CI (`--base`, strict target)
  `_config` also reads `ado.stack.json` and `ado.kits.lock.json` from the target commit through `strict._blob_at` (256 KiB each;
  `Config.target_profile_blob` / `target_lock_blob`), fails closed on an invalid one (exit 3 for `gate`, `branch --id-from` and `strict mode`,
  2 for `strict check`; the gate comment says "target branch trust files are invalid") and judges with them, so editing them does not change
  the command or the verdict in that PR's own run. Because the PR's copies become the target's on merge, `strict check` also refuses a head
  profile that is invalid, a draft, changed, deleted or inconsistent with the head's `strict.test_command` when the head differs from the
  target (same ids, detail "at the head commit"; also at bootstrap, when the target has no profile). Any PR that edits the profile, the
  lockfile or `strict.test_command` carries `trust-introducing PR: review ado.stack.json as code` (`warnings` in `strict check`, a line and the JSON key `trust_note` in the gate
  comment); once merged the edit is in force with no note (an approved hash is computable by anyone), so review these files as code.
  `strict verify` applies no stack or kit rule. `stack.profile_for`, `stack.lock_error`. See `docs/stack-kits.md` "Strict rules and CI".
- **Upgrade note (strict projects):** a strict project with no `ado.stack.json` now fails `strict check`. That includes link-on-merge
  `ado done` (merged tree, local config), which refuses `stack-profile-missing` for existing strict projects until the profile PR merges:
  run `ado stack init --seed <name>` (or `speckit.ado.stack`, once installed), then a human runs `ado stack approve --apply`, and merge it first.
- Config: `mode` (`"advisory"` default, or `"strict"`) and the `strict` object (`test_command`,
  `test_timeout`); in strict mode the effective `gate.block` and `review_block_on` are the
  project's lists plus the strict floor. Invalid values are a config error.
- Decisions live in their own file, `<dod_path>/Decisions_<id>.md` (a title, a format note, one `D<n>` line per entry), not in
  the ledger. The earlier unreleased form, `D<n>` lines under `### Decisions & assumptions (development)` in ledger
  section 2, is dropped: a ledger from those builds is not read for decisions, and the ledger template no longer seeds the
  heading (section 2's Assumptions bullet points to the file instead).
- `ado decisions <id> --add "<text>" --kind decision|assumption [--why "<reason>"]`: records one assumption or decision (a
  local write, atomic; creates the file; prints `{"id": "D3", "file": "<path>"}`). The next id is the highest in the file + 1.
  `@mention`s and e-mail addresses are replaced by `@user` / `[email]`; control characters, `<!--`, `-->` and code fences are
  refused (exit 2) without echoing the text; `override` is not accepted. The plan and implement prompts require it for every
  assumption and decision (each `assumed:` answer too).
- Workflow `ado-lifecycle` wiring for strict mode (no shell conditionals: in an advisory project the strict steps are no-ops and `decisions-loop` is a no-op when there is no decisions file; when there is one the post happens in every mode, behind the gate): `strict-check-implement`
  (`ado strict check <id> --phase implement` before `implement`), `decisions-loop` after `implement` (preview, gate `approve-decisions`, apply with
  `--expect-plan` and the `.new` guard, then `decisions-applied`; posts the decisions as one new comment), `strict-verify` (`ado strict verify <id>`)
  before `sync-loop`, and a last gate `wait-po-validation` after the PR loop (wait for the product owner's verdicts, recorded by a human with
  `ado decisions <id> --resolve`, then commit and push the resolved lines to the PR branch before the merge; in strict mode `ado done` refuses while any is open); the run still ends at the open PR. `strict-verify` sets `timeout: 7200` (the engine's 300 s shell default would kill the test runs). The workflow README and its recovery section describe them.
- `ado decisions <id> --add "<text>" --kind decision|assumption --supersedes D<n>`: a new decision answers a PO rejection. The line's text starts with `supersedes D<n>: ` (shown to the PO in the posted comment; the grammar is unchanged and the old line is never edited); `parse_decisions` adds `supersedes` to such an entry and `decisions.effective` drops a decision a live superseder (not rejected, or itself answered by a live one) answers. `decisions.unposted`, strict `pr`/`done` (`decision-rejected`, `decision-open`) and `ado gate` `open_decisions` use the effective set; a rejected superseder re-activates the original. New strict findings `supersedes-unposted` (never overridable) and, under `decisions-invalid`, an unknown target, an override target, a superseder that is not higher than its target (self-reference, cycle) or a `supersedes` line behind a list number or `> `. `--supersedes` refuses (exit 2) a target that is missing, validated, an override, already live-superseded, or open and never posted (`post D<n> first`). `supersedes-unposted` covers every line that supersedes another, whatever its status, and every open decision a superseder retires, when its `posted:` marker is missing or no longer matches (a superseder's marker hashes its id too); the marker is never checked against ADO in this release. `ado decisions <id> --resolve` on a superseded decision adds a `notice` to its JSON. Advisory output is unchanged for a file without `supersedes` lines; with an invalid one the gate lists `?`.
- `ado decisions <id> --post --if-any` (also with `--plan-out`): a missing decisions file is a no-op in every mode (exit 0, nothing to post, `note: "no decisions file; nothing to post"`,
  and an `--apply` writes nothing); without `--if-any` a missing file is still exit 2. The workflow's `decisions-loop` passes it. It needs `--post` or `--plan-out`.
- Prompts: `commands/implement.md` says the workflow runs the strict check, posts the decisions and repeats `ado strict verify` (and that `implement` runs verify itself before handing over, with `--workflow` too);
  `commands/plan.md` says the workflow posts the recorded `D<n>` lines; `commands/review.md` plane 7 flags an unrecorded decision in strict mode (`major`);
  `commands/done.md` names the `wait-po-validation` gate. All strict-mode only; advisory behaviour is unchanged.
- `ado decisions <id>` (read-only, local): the file's open, validated and rejected `D<n>` lines as JSON (exit 2 when there
  is no file yet).
- `ado decisions <id> --post` (dry run; `--plan-out`, `--apply`, `--expect-plan` as for `questions --post`): one new
  `[spec-kit-ado:decisions]` comment with every open `D<n>` line not yet posted or edited since; apply writes
  `posted: <comment id>:<hash8>` into exactly those lines of the decisions file (atomic). Typed `@mention`s and e-mail
  addresses are replaced by `@user` / `[email]`; lines are cut at 500 characters.
- `ado decisions <id> --resolve D<n> --validated|--rejected [--by ROLE] [--note TEXT]`: a human records the PO's verdict on
  one decision line (local edit of the decisions file, atomic, keeps `kind`, `why`, `posted`, `used`; a resolved line needs
  `--force`). Refuses (exit 2) without a terminal so an agent shell cannot do it by accident; not a security boundary (the
  strict gate, the decisions comment the PO sees and the git history are the enforcement). `--by` is a role (default `PO`),
  `--note` at most 300 characters without `<!--`, `-->` or code fences.
- `ado done` attaches `Decisions_<id>.md` next to the ledger when it exists (same allowlist; a file already attached by
  name is skipped, so a re-run never attaches it twice; the plan lists it and the dry-run JSON gains `decisions_file`).
  Without the file nothing changes.
- Gate: `open_decisions` (ids of open or rejected `D<n>` lines of `Decisions_<id>.md`) is reported only when that file has
  `D<n>` lines, joins the checks `gate.block` can name, and adds one line to the CI summary; `?` stands for an unparsable
  line, an unclosed fence or comment, or an unreadable file; a repo without the file keeps the v0.2 output.
- `ado strict check <id> --phase implement|pr|done` (read-only): in strict mode exits 2 with JSON findings (`rule`, `detail`, `fix`)
  until the spec dir has `spec.md`, `plan.md`, `tasks.md`, section 2 has no `_(fill in` placeholder, no question is open, every
  `tasks.md` task has a board Task (an unreadable board is a finding) and `strict.test_command` is set; advisory mode returns `ok: true`.
- Strict mode, L1 order evidence (`pr` and `done` phases of `ado strict check`): each ledger section 3 AC row needs
  `red <sha> → green <sha>` in its `status` cell (7 to 40 hex characters; `->` also works), resolved in git with replace refs ignored and a
  30 s timeout. Rules: `evidence-missing`, `sha-unknown`, `merge-commit`, `red-not-ancestor` (also one commit used twice),
  `red-touches-code` (a file that is neither a test file nor exactly `<dod_path>/DoD_<id>.md`, `<dod_path>/Decisions_<id>.md` or the valid spec dir's `tasks.md`), `red-no-test`, `green-not-on-branch`.
  Findings carry counts and at most 12 hex characters of a SHA, never a file name. `commands/implement.md` asks for two commits per AC in strict mode. L1 needs a full clone (`fetch-depth: 0`).
- Strict mode, `ado strict verify <id> [--record|--no-record] [--base REF]` and the verification record: runs L1, then `strict.test_command` at each AC's red commit (must exit non-zero) and green commit (must exit 0), once per distinct commit, then at `HEAD` (must exit 0; reused when `HEAD` is a recorded green); refuses with `red-passes`, `green-fails`, `head-fails`, `timeout`, `test-command` or `runner-error` (exit 2). `--record` (default) appends `### Verification — <date> @ <head sha>` to ledger section 4 (before `## 5.`; per AC the red and green SHAs and exit codes, `- head: <12 hex> exit <n>`, `command-sha256`, `seconds`; earlier headings get ` (superseded)`; atomic write); `--no-record` (CI) writes nothing. `ado strict check` (`pr` and `done`) validates the record with `record-missing`, `record-stale` (a later commit touching more than the ledger, `Decisions_<id>.md` or `tasks.md`), `record-incomplete`, `record-failed` and `record-command-mismatch` (the record's `command-sha256` differs from the sha256 of the test command in force, the target branch's in strict CI); the `done` flavour skips ancestry so a squash merge passes. Review staleness shares its git diff with `prs.only_ledger_since`; `prs` git calls now have a 30 s timeout, no pager and ignore inherited `GIT_DIR`/`GIT_INDEX_FILE`/`GIT_WORK_TREE`, and a hung or missing git is a refusal (a `record-stale` finding in strict). `seconds` stops at the process exit, not the output drain. Git LFS filters do not run in the test checkout (global git config is ignored): LFS files are pointer files.
- Strict mode, `ado strict override <id> <rule> --reason "<text>" [--apply]` and the `pr` and `done` decision and review rules: a human records a `kind: override` decision `skip <rule>` (`test-command`, `l2` = `record-missing`, `record-stale`, `record-incomplete`, `record-failed`, `head-fails`, `green-fails`, `red-passes`, `timeout`, `runner-error`, `open-question` or `placeholder`; never L1, `spec-missing`, `plan-missing`, `tasks-missing`, `decision-rejected`) in `Decisions_<id>.md`: refuses (exit 2) unless stdin is a terminal, before anything is read or written (a guard against accidents, not a security boundary), reason 20 to 300 characters, atomic write read back whole, no `--apply` prints the line. `ado strict check` drops the findings an override covers (a `rejected` override covers nothing) and returns `overrides_used`; `decisions.stamp_used` (called by the apply paths of `pr create` and `done`) adds ` · used: <date>` once. New `pr` and `done` rules: `decision-rejected`, `override-unposted`, `decisions-invalid`, `review-missing`, `review-blocks` (`prs.review_blocks` with the strict review list), and for `done` `decision-open`; `done` (squash merge) runs only the git-free `evidence-missing` of L1 (it works with the red and green commits gone), skips the staleness of the record and the review (a detached `HEAD` is accepted there) and checks an optional `PR head <sha>` in a section 3 commit/PR cell against the record; a duplicate `D` id or a `D<n> … → status` line that is not a decision line is `decisions-invalid`; the `posted:` marker of an override hashes its id, text and reason, `ado decisions --post` also posts validated overrides, `--reason` refuses an address or @mention, a second override of a rule in force is refused; markers are not checked against ADO. `strict._git` now shares `prs.git_env` (no inherited `GIT_DIR`, `GIT_INDEX_FILE`, `GIT_WORK_TREE` or other selector). `l2` also covers `test-command`, so one `l2` override lets an unset `strict.test_command` pass. `ado strict verify` honours overrides like `check`: with an `l2` or `test-command` override in force it runs no tests, records nothing and exits 0 with `verification skipped: overridden` (and `overrides_used`) (with `--no-record`, as in CI, an override in force is instead the finding `override-in-force` and exit 2 unless `--allow-override` is passed).
- Strict mode wired into the verbs: `ado pr create` (preview and `--apply`) and `ado done` (preview and `--apply`) first run `strict check` for the phase (`pr` with `--head` and a base ref derived from `--base`: `origin/<base>` when it exists, else `<base>`); a finding is exit 2 with `error: strict check failed: ` and each rule id with its fix, the findings as JSON, no plan file, no request and no push. `ado strict check` gains `--base REF` (red and green must lie in `REF..head`, `not-in-range`; resolved as `refs/remotes/REF`, `refs/heads/REF`, then a full commit id, never a tag; an unknown base is `base-unknown`, never overridable; the `pr` phase defaults to the `origin/HEAD` branch and `pr create` refuses a `--head` that is not the checked-out commit) and `--head REF` (a branch or a commit id, so CI on a detached checkout passes the head SHA). `ado gate` blocks on `cfg.effective_gate_block()` and `pr create` on `effective_review_block_on()` (strict adds `blocker`). The apply paths stamp ` · used: <date>` on the overrides they relied on (`decisions.stamp_used`, atomic, read back; a failed write is exit 2 before anything is sent; a refused run or a preview stamps nothing; the stamp is an uncommitted change, commit it). `ado done` needs the merged working tree to contain the ledger and the decisions file.
- Strict mode, L3 AC fidelity verdicts: the `speckit.ado.review` plane `AC fidelity` (strict mode only; a fresh reviewer sees only ledger section 1's AC text and the mapped test bodies) writes `### AC fidelity — <date> @ <sha>` into ledger section 4 above `## 5.` with one `- AC-<n>: asserts|partial|does not assert — <reason ≤ 200 chars>` line per AC (a model reading, not proof; the previous block gets ` (superseded)`). `strict.parse_fidelity` reads the latest live block fail-closed; `ado strict check --phase pr|done` adds `fidelity-missing`, `fidelity-fails`, `fidelity-invalid` and, for `pr`, `fidelity-stale`, none overridable; `partial` adds a `warnings` list (`fidelity-partial`, only when non-empty). The review is now eight planes (the eighth in strict mode only).
- Strict mode, L2 test runner (library functions `strict.run_at` and `strict.scrubbed_env`, used by `ado strict verify`): runs `strict.test_command` at a
  commit in a temporary checkout, a throwaway `git clone --shared --no-checkout` outside the repo (origin removed, hooks off, no shared config or index),
  removed in every case (also on Ctrl-C, and read-only directories such as Go's module cache); every git call and the command get an allow-listed environment
  (`PATH`, `LANG`, `LC_*`, `TERM`, `TMPDIR`, toolchain variables, an empty temporary `HOME`, no `GIT_*`) with a deny-list pass for `*_TOKEN`, `*_PAT`, `*_SECRET`, `*_KEY`,
  `*_PASSWORD`, `*_CREDENTIALS`, `AWS_*` and the ADO, GitHub and SSH credentials; no stdin, the whole process group killed at the timeout, output never returned
  (line counts only, constant memory). Hygiene, not a sandbox. The test command is code from your project: CI must run it without secrets.
- `ado strict mode [--base REF]` (read-only, local, no token, no network): prints `{"mode": "strict|advisory", "source": "target branch|pull request"}` with the same config rule as `ado gate --base` (a strict `ado.config.json` on the target branch decides; otherwise the PR's own mode); exit 3 when the target config is invalid or unreadable, or the base is no commit here under a strict PR config (CI fails closed); an advisory PR config with an unresolvable base stays advisory.
- Shipped CI templates enforce strict mode: both gate templates ask `ado strict mode --base origin/<target>` first (exit 3 and every exit other than 0 and 2 fail the job; exit 2, no usable config, is advisory as before). Under a strict target a PR with no story id fails instead of skipping the gate (the Azure story-id step now passes `--base` to `ado branch --id-from`), and `ado strict check <id> --phase pr --head <head sha> --base origin/<target>` runs after the gate without `ADO_TOKEN` (a separate step on GitHub, `env -u ADO_TOKEN` in the Azure script), puts rule ids only in the PR comment or thread and fails the job on any non-zero exit. The link-on-merge templates report a strict `ado done` refusal as `strict done refused: <rule ids>` (at most 20 ids, 40 characters each, `[A-Za-z0-9_-]` only; never the findings' text) instead of `check ADO_TOKEN and config`, and fail the job once every story has been handled (the refused story is not moved by `transition-state`; the others are). GitHub link-on-merge now checks out `merge_commit_sha` at `fetch-depth: 0` with `persist-credentials: false`. With an advisory or absent target config the templates behave as in v0.2. Still the owner's job: protect `.github/workflows/*`, the caller workflow and `toolkit-ref`, the Azure pipeline YAML and `ado.config.json` (branch rules, `CODEOWNERS`). The Actions and Azure agents have not been run in this project's environment (billing): template logic is covered by `bash -n` and real-bash tests with a fake `ado`.
- Optional secret-free CI re-run job, SHIPPED UNRUN: `.github/workflows/ado-strict-verify.yml` (reusable, `workflow_call`, inputs `toolkit-ref` and `allow-override`) and `pipelines/ado-strict-verify.yml` re-run `ado strict verify <id> --no-record --base origin/<target>` on the PR. No secret is declared and no token is mapped (`permissions: contents: read`; `persist-credentials: false` / `persistCredentials: false`, `fetch-depth: 0` / `fetchDepth: 0` on every checkout); the story id is read as the gate does; it does nothing (a notice) unless the target branch's config is strict, since otherwise the test command would come from the PR; `ado strict mode` exit 3 and any other unexpected exit, a missing story id under a strict target, and any non-zero verify exit fail the job, logging sanitised rule ids only, never test output. Plainly: it re-runs the tests at the recorded commits under the target branch's command; it fails while an `l2` or `test-command` override is in force unless `allow-override` is set; it cannot be satisfied by hand-written records; it does not show a test is meaningful (the PR still writes the tests at those commits; the fidelity verdicts and the human review gate cover that). An override in force skips the run, so `ado strict verify --no-record` exits 2 with the finding `override-in-force` (naming the override ids) unless `--allow-override` is passed; the input `allow-override` (default false) adds that flag. It judges the PR head commit (as the gate's `--head`), not the merge commit. **Security:** it runs the project's test command on the PR's code, so a hostile PR can still run arbitrary code on the runner (no secret to steal, but network and resources): call it on `pull_request` only, never `pull_request_target` (the workflow refuses any other event), keep approval for fork pull requests and use ephemeral runners. **Shipped unrun:** GitHub Actions and Azure hosted agents are billing-blocked here, so it is covered by structural pins, `bash -n` and real-bash shim tests only. Docs: `docs/ci-github.md`, `docs/ci-azure-pipelines.md`, `pipelines/README.md`.

### Changed

- Strict mode: `ado update <id> --state <done_state>` (or `--field System.State=<done_state>`, compared case-insensitively with the configured `done_state`) is refused (exit 2, nothing sent, pointing to `ado done <id>`), so the strict `done` check cannot be skipped by moving the story by hand. Other states and advisory mode are unchanged. The link-on-merge templates' `transition-state` / `transitionState` equal to `done_state` therefore fails under strict (a warning, the story is not closed): use `close-story: true` / `closeStory: true`, so `ado done` runs the strict check.
- Strict mode: `ado strict verify --no-record` (the CI re-run) fails with the finding `override-in-force`, naming the override ids, while an override is in force (an `l2` or `test-command` override skips the run); `--allow-override` accepts it, and both re-run templates take an `allow-override` input (`allowOverride` on Azure Pipelines, default false) that adds the flag.
- Strict mode: `strict check` (`pr` and `done`) reports `record-command-mismatch` when the record's `command-sha256` is not the first 12 hex of the sha256 of the test command in force (the target branch's in strict CI; an unset command with a record present is the same finding; `l2` covers it).

### Fixed

- The `ado decisions --post` preview says `D1 gets` for one line and `D1, D2 get` for several (it printed `get` for one).
- `done_state` must be a non-empty string, like `story_type` and `removed_state` (a `null`, a number or `""` was never valid and made `ado update` crash or refuse in the new strict check): an invalid value is now a config error with the fixed text `'done_state' must be a non-empty string`.

### Security

- CI trust: `ado gate --base REF`, `ado strict check --base REF` and `ado strict verify --base REF` read `ado.config.json` from the PR's target branch first (`Config.load_from_ref`, `git show` at the full ref through the hardened git helper, same validator as `Config.load`, at most 1 MiB, regular files only; `refs/remotes/REF`, then `refs/heads/REF`, then a full commit id, never a tag). When it says `mode: strict`, the whole config is the target's, so a PR cannot lower `gate.block`, re-point `strict.test_command` or widen `gate.tests`; the `gate` summary gains `config: target branch (strict)` (and the JSON `"config"`) only then. `ado branch --id-from NAME --base REF` takes the config the same way (the story-id step of the templates, which now passes it). An unreadable or invalid target config or a failing git call exits 3 for `gate` and `branch --id-from` (2 for `strict check` and `strict verify`, an `error:` line and no JSON); a base that is no commit here does the same when the PR's own config is strict and keeps the v0.2 path when it is advisory. The gate walks its commit range from the resolved commit id in strict mode (a tag cannot move it); the summary says `config: pull request` when `--base` is given and the PR's strict config judged it. Config nested too deeply is a config error, not a crash. Only the repo-root `ado.config.json` is trusted (a non-default `config-path` falls back to the PR's silently) and the CI definition is PR-controlled: protect both. No target config, or an advisory one, keeps the PR's config; without `--base` nothing changes. `strict verify --base` now resolves REF like `strict check --base` (a tag can no longer shadow it). Protect `ado.config.json` on the target branch with branch rules or `CODEOWNERS`.

- Attachments no longer follow symlinks: `ado attach` and `ado done` refuse a path whose last component is a symlink or that
  is not a regular file, refuse a file over 5 MiB, and upload the bytes read once through one `O_NOFOLLOW` descriptor (a
  file swapped for a link after the check is not followed). `ado done` also reads the ledger and `Decisions_<id>.md` as
  confined as the gate does (real path under the working tree, 1 MiB, UTF-8) and exits 2 before any request when one is
  not, so a committed link cannot make CI upload another file.

### Advisory-mode additions in 0.3.0

An advisory project (`mode` absent or `"advisory"`) keeps every v0.2 output except these seven additions, which the release owner accepted:

1. New ledgers get the decisions-file pointer sentence in section 2's Assumptions bullet (`Each is also recorded in Decisions_<id>.md`).
2. `gate` adds `open_decisions` only when a decisions file exists (the JSON field, and one line in the CI summary).
3. `done` adds `decisions_file` and a plan step and attaches `Decisions_<id>.md` only when the file exists (the plan renumbers "set state"; the apply makes a second attachment request).
4. The usage text lists the new verbs `decisions` and `strict`.
5. `done` and `attach` refuse symlinked or oversized files (`done` reads the ledger as the gate does: a symlinked or over-1-MiB ledger v0.2 accepted is now refused).
6. `gate` exits 3 for an invalid target-branch config when `--base` is given, in an advisory repo too. To recover from exit 3 on an invalid target-branch config, fix the target config through an admin-bypass merge, or run the gate once without `--base`.
7. The plan and implement prompts record decisions and the workflow posts them behind a gate in every mode when a decisions file exists, so an advisory product owner gets a `[spec-kit-ado:decisions]` comment.

## 0.2.0 — 2026-10-29

Feature release: the story's discussion and attachments are read, open questions go to the
board, the feature branch is named from the story, and the gate, review and PR steps close
the holes found in end-to-end runs.

### Added

- Workflow: `ado-lifecycle` takes an `integration` input (default `auto`) and passes it to
  every command step, and lists the integrations it was written for under
  `requires.integrations.any`.
- Workflow: each write (questions, task push, task sync, PR) is a preview, gate and apply loop
  that re-previews and re-asks when the apply step finds the plan changed.
- Workflow: after `pull` and its review gate, the open questions are posted to the story.
- CLI: `ado comments <id>` reads the story discussion (all pages, author names removed,
  mentions replaced) into `.ado/context/<id>.md`, which is gitignored.
- CLI: `ado attachments <id> [--download]` lists the story's attached files and downloads the
  ones on `attachments_allow` into `.ado/attachments/<id>/`.
- CLI: `ado questions <id>` lists the ledger's open questions (section 2 format
  `- [ ] Q<n>: <question> → <answer | _(open)_> · owner: <role>`); `--post` previews and
  posts them as one story comment, and re-posting edits that comment instead of adding one.
- CLI: `ado branch <id>` names the feature branch `<branch_prefix>ab<id>-<slug>` (`--apply` creates it, or
  renames the Spec Kit branch made for this spec); `--id-from <name>` prints the story id of a branch;
  on Azure Repos `--link` adds the pushed branch to the story's Development section.
- CLI: `ado link <id> --parent <n>` sets the story's parent.
- CLI: `--expect-plan <file>` on `push`, `done`, `pr create`, `questions --post` and `branch --link`
  re-renders the plan and refuses (exit 2, writes `<file>.new`) when it no longer matches the
  approved preview.
- Gate: `open_questions` (open items in ledger section 2) and `commits_without_story` (commits in
  `<base>..HEAD` without `AB#<id>`) join the checks `gate.block` can name; `ado gate --base REF`
  sets the base.
- Gate and review commands accept `--workflow` and then never ask the human.
- Review: seven planes, adding Fresh eyes (diff and commit messages only) and Story and business
  context (story, acceptance criteria, `.ado/context`, ledger answers).
- Config: `branch_prefix`, `removed_state`; `ado setup` detects `story_type`, `done_state` and
  `removed_state` from the project and adds `.ado/` to `.gitignore`.
- `SECURITY.md`, a `license` field in `pyproject.toml`, and an `.extensionignore` that keeps
  development-only files out of consumer installs.

### Changed

- `ado push` shows each task's state change and old title in the preview, cuts titles to
  255 characters (marked, stable on re-runs), reports a child that sits in `removed_state`
  as removed, flags extra children carrying the same task tag as duplicates, and keeps the
  created work item id when a later step fails.
- `ado link <id> --parent <n>` replaces `--field System.Parent=...`, which ADO does not apply: that field
  now errors and says to use `ado link --parent`.
- `ado done --apply` refuses when it cannot read the story first, instead of writing blind.
- The GitHub gate finds its PR comment by a hidden marker (`<!-- spec-kit-ado:gate -->`) instead of editing
  the last comment, so the first run after upgrading posts a new comment.
- `ado branch --link` needs a PAT with Code: Read (it looks up the repository id), and opening or commenting
  on Azure Repos PRs needs a token allowed to write to Azure Repos; both are documented in
  `docs/configuration.md`.
- `speckit.ado.plan` and `speckit.ado.pr` read the PO's replies (`ado comments`) and update the answers in
  ledger section 2; `speckit.ado.status` lists the open questions (`ado questions`).
- `ado done` is idempotent: a story already in the done state, or a ledger already attached,
  is not written again, and the plan says so.
- The DoD ledger is written atomically; a failed refresh keeps the old ledger.
- The gate keeps its offline checks when no token is available.
- The client honours `Retry-After` on HTTP 429 (capped at 60 s), retries writes on 429 only, and
  names an invalid or under-scoped PAT when ADO answers with a sign-in page (203/HTML).
- Commits made by `speckit.ado.implement` end with `AB#<id>`.
- `speckit.ado.pr` commits the spec directory and ledger before the preview, links the ledger in the PR
  body at that commit, and `speckit.ado.review` records the commit it reviewed.
- A task-push plan too long for the approval gate's 200-line display starts with a warning line that
  points at the full file.
- A workflow run that ends with unfinished tasks says `INCOMPLETE: <tasks>` and the review gate says to reject it.
- A hard-coded secret in a diff is a `blocker`; a new review marks earlier ones in the ledger `superseded`.
- `speckit.ado.pull` takes the acceptance-criteria length (`ac_chars`, `ac_thin`) from `ado dod` instead of counting by hand.
- Docs: README, AGENTS.md, `rules/hitl.md` and `docs/configuration.md` cover every new verb, flag and key.

### Fixed

- `VERSIONING.md` records that `v0` follows breaking changes while the major version is 0 (pin an exact tag for stability; rollback moves `v0` back), and the go-public checklist in `docs/catalog-submission.md` now publishes from a fresh squashed repository with identity, scrub, tag-content and `v0`-last checks.
- An emptied `tasks.md` no longer slips through (live recheck: `implement` left an empty file and a normal PR opened). `ado pr create` treats a `tasks.md` with no tasks as unfinished while the board still tracks `[sk:…]` Tasks for the story (refuses; `--allow-incomplete` opens a draft that says why), the `ado push` preview opens with a warning when every tracked Task would be an orphan, and `commands/implement.md` says never to delete or reword task lines.
- `ado questions --post` lists the questions closed as `assumed:` with their assumption under "The team assumed these; reply if one is wrong:". Before, re-posting after `plan-with-answers` rewrote the comment to only the new question and the product owner never saw what had been settled for them (found in the live recheck).
- A review block written below ledger section 5 (seen in the live recheck, where it stopped the PR step) is now prevented and diagnosed: `commands/review.md` and `commands/gate.md` say to insert it directly above `## 5.`, and `ado pr create` says when a `### Review` block exists outside section 4.
- `workflows/ado-lifecycle/README.md` documents `specify workflow status` and `resume` (it re-runs only the unfinished step), that a gate pauses the run without a terminal, and the manual order for catching a stopped run up (sync Tasks, gate, review, PR body, resume).
- Review reads a fresh story discussion: the workflow runs `ado comments` (new step `refresh-context`) right before `review`, and `commands/review.md` makes the refresh required, or the `Story fit:` line must say `story discussion not refreshed`. Before, the review used a stale `.ado/context/<id>.md` and missed a product-owner comment posted after pull.
- Plan and implement agree on PO answers: behaviour from an answered `Q<n>` (the product owner's answer, or an assumption the operator accepted at `wait-po-answers`) counts as covered when the spec and the task cite `(Q<n>)`. Before, the plan folded a PO answer into new requirements and tasks and implement then refused them as "not in the AC", ending the run with `INCOMPLETE`. Behaviour with neither an AC nor a cited answered question still stops and asks. `commands/tasks.md` keeps the `(Q<n>)` citation in the task text.
- Workflow: a question the second plan run (`plan-with-answers`) raises is now posted to the story. A new `answers-questions-loop` (preview, gate, apply) follows that step; before, `commands/plan.md` promised a post step that did not exist, and the question stayed open, unseen by the product owner, and blocked `ado done`.
- `ado pr create --apply` on Azure Repos reads the new PR's work items back and adds the story's Pull Request link when Azure dropped it (seen once in the live rerun: the request carried `workItemRefs`, the PR came back unlinked). The result gains `work_items_linked`; if the check or the repair fails, a note goes to stderr and the PR result is still returned. The PR plan says on Azure that the link may be added, and `commands/pr.md` and `rules/hitl.md` count it under the same yes.
- Docs and hints: `ado setup` and `ado branch --link` read the project record, which Microsoft documents as needing **Project and Team: Read**; the PAT scope lists now say so (previously only Code: Read was named for `--link`).
- `ado comments` also scrubs plain-text email addresses typed into a comment (`[email]`), not only `@mention` links, before the text reaches the JSON and `.ado/context/<id>.md`.
- `ado branch` slug: the 40-character cap now drops a half-cut last word (`…-by-status-and`, not `…-by-status-and-ro`); a single word longer than the room is still cut hard.
- Workflow: approving `wait-po-answers` without the product owner's replies no longer halts the run after
  `implement`. The gate said you could accept the plan's assumptions, but the unanswered `Q<n>` lines stayed open and
  `gate-check` (with `open_questions` in `gate.block`) stopped the run late. `plan-with-answers` now gets
  `--close-assumed` and records each still-unanswered question as `- [x] Q<n>: … → assumed: <assumption> · owner: PO`
  (kept in ledger section 2 for review); the gate message says so.
- Workflow: questions the agent raises reach the product owner. `ado questions --post` sends only ledger
  `Q<n>` lines, but `plan --workflow` wrote its open points as `Needs human decision` prose and `pull` left
  spec ambiguities in the spec, so nothing was posted and the run went on with them unresolved. In
  `--workflow` mode both commands now write what the product owner can answer as
  `- [ ] Q<n>: <question> → _(open)_ · owner: PO`, and `ado-lifecycle` posts them again after `plan`
  (`plan-questions-loop`, behind its own gate), then waits at a `wait-po-answers` gate and re-runs `plan`
  (`plan-with-answers`) so the replies reach the ledger and the plan before `tasks`. The run does not wait
  by itself: the gate is where the operator waits.
- Link-on-merge (GitHub and Azure) closes the story before it applies `transition-state`. With the
  transition equal to `done_state` the old order moved the story first, so `ado done` saw it closed,
  attached nothing and the log still said "ledger attached, story closed". The log now reports what
  `ado done --apply` did, and a story the close left in `done_state` is not transitioned again.
  A story that was already in `done_state` is never moved by `transition-state`, even when the ledger
  attach fails or the ledger is not ready (the log says "state left unchanged"); `ado done` now always
  prints `already_done` (also on a dry run and when it exits 1). A story that is not done keeps the old
  behaviour. The check runs only where `ado done` runs: with `close-story` on and a ledger present.
  `ado done --apply` on a story that is already in `done_state` attaches the ledger when it is not
  attached yet (state untouched, and the preview says so) instead of skipping it.
- The workflow clears prepared PR files before `pr-prepare`, and its gate step is `speckit.ado.gate` followed by a
  `gate-check` that halts on a blocking result.
- Ledger: a `## N.` heading inside a code fence or `<!-- -->` comment is no longer a section. `ado done`, the
  gate, `ado questions` and `ado dod --refresh` read the real sections only (a fenced `## 5.` ticked `[x]` could
  report "ready to close"; a fenced `## 2.` made `--refresh` drop sections 2-3). An unclosed fence or comment makes
  `done` not ready, adds a `ledger_unclosed` note to the gate (an `open_questions` finding for `gate.block`), and
  makes `--refresh` refuse (exit 2) unless it was left open in section 1, which `--refresh` still repairs.
- A review stays fresh across the `speckit.ado.pr` step-2a commit. The spec dir was exempt only if its
  `spec.md` or `tasks.md` was already committed at the reviewed SHA, but the documented flow commits it
  after the review, so `ado pr create` called every review stale. A spec dir that did not exist at the
  reviewed SHA now counts when the head commit has a regular `spec.md` or `tasks.md` in it. The path
  still comes from the ledger at the reviewed SHA, and any other file changed after the review is stale.

### Breaking

- CI branch guards and `ado branch --id-from` accept only `<branch_prefix>ab<id>-<slug>` (for example
  `feature/ab1001-count-active-members`) or `AB#<id>` in the name. The old "any digit run" rule and plain
  `feature/<n>-…` names no longer match, so Spec Kit's `NNN-slug` branches are never read as stories.
  The reusable branch guard reads the prefix from `branch_prefix` in `ado.config.json` (there is no
  `branch-prefix` input), so CI and `ado branch` cannot disagree. Rename branches that still use the old
  shape, or set `branch_prefix` to match.
- `AB#<id>` matching is stricter in the GitHub gate, the Azure gate, link-on-merge (GitHub and Azure) and the
  branch rule: it is case-sensitive, a whole token (not glued to a letter or digit before it), and the id
  starts with 1-9. `ab#12`, `fooAB#12` and `AB#007` no longer match, so the gate is skipped and
  link-on-merge does nothing for them.
- A `gate.tests` glob with an unbalanced `[`, a glob over 200 characters, or a regex over 500 characters (or one
  that does not compile) is now rejected when the config loads (exit 3 for `ado gate`). Config errors name the
  entry or field by position and no longer print the offending text.
- `ado gate` (and `ado branch --id-from`) exit 3 for an invalid `ado.config.json`, and the GitHub gate, the
  Azure gate and the workflow `gate-check` fail on it instead of staying advisory. Unknown `gate.block` or
  `review_block_on` names are rejected, and control characters in any config string are rejected.
  A missing config file or token still exits 2 and stays advisory.
- `ado pr create` refuses when `review_block_on` is set and the review is missing, blocking or stale, and when
  the spec directory's `tasks.md` has unticked tasks. Use `--allow-incomplete` to open a draft PR.
- `ado setup` now detects and writes `done_state`, `story_type` and `removed_state` from the project. A
  config written before this keeps the `Done` default when `done_state` is absent, which exists only on
  Scrum and Basic projects, so on Agile (`Closed`) and CMMI projects `ado done` and `ado push` stay broken
  until you re-run `ado setup`. `ado setup` fills in only absent keys, so a config copied from the 0.1.1 template, which
  holds `"done_state": "Done"`, keeps it: delete `done_state` (and any wrong `story_type` or `removed_state`) before
  re-running it. The shipped template no longer sets `story_type`, `done_state` or `removed_state`.
- Gate: acceptance criteria are now counted from the `### Acceptance Criteria` lines in ledger section 1 (AC-1 is the
  first line), not from the rows of section 3. A 0.1.1 ledger whose section 3 is empty, still holds the
  placeholder row or has fewer rows than section 1 has AC lines now reports the missing ones as unmapped, and blocks
  when `ac_traceability` is in `gate.block`. (An empty section 3 used to be "not evaluated": it is now checked
  against the AC lines in section 1.) Migrate: give each AC a row in section 3
  naming its test, e.g. `| AC-2 | ... | tests/test_x.py::test_y | ... |`. A ledger with no AC lines and no AC rows is
  still not evaluated.
- Gate: Python tests named in section 3 are now checked one by one with `ast`, so a mapped test whose own body has no
  assertion is an `assertion` finding (blocking when `assertion` is in `gate.block`); other languages keep the
  file-level check and add a note.
- `ado gate` reads only regular files of at most 1 MiB that resolve under `--src`, and the ledger must live under
  `--src` too. A symlinked test file or ledger, a file outside `--src` (for example `--src src` with the ledger in
  `dod/` beside it) and an oversize file are no longer read: the gate reports the ledger unreadable (advisory) or
  counts the file as skipped. Run it with `--src .` from the repo root, as the CI templates do.
- Ledger sections are found fence-aware: a `## N.` heading inside a code fence or `<!-- -->` comment is not a section.
  A ledger with an unclosed fence or comment is now "not ready" for `ado done`, adds a `ledger_unclosed` note to the
  gate (an `open_questions` finding, so it blocks when `open_questions` is in `gate.block`), and `ado dod <id> --refresh`
  refuses it (exit 2) unless the fence was left open in section 1. Close the fence, or use `--force`.
- `ado done <id> --apply` on a story already in `done_state` now attaches the ledger when it is not attached yet (the
  state is left alone), and it refuses (instead of writing blind) when it cannot read the story first.
- `--field System.Parent=...` (on `ado create` and `ado update`) is rejected with an error that points to
  `ado link <id> --parent <n>`.
- Link-on-merge (GitHub and Azure) now runs `ado done` (when `close-story` is set) before `transition-state`, and does
  not transition a story the close left in `done_state`. A `transition-state` equal to `done_state` therefore closes the
  story with the ledger attached instead of moving it first, but only when `close-story` is true and the ledger is ready.
- The `ado-lifecycle` workflow has new inputs, steps and gates: an `integration` input (default `auto`), the `questions-loop`,
  `plan-questions-loop`, `wait-po-answers` gate and `plan-with-answers` steps, and `gate-check`, which halts the run on a
  blocking gate or an invalid config. A run now stops at `wait-po-answers` until you approve it, and in `--workflow`
  mode `pull` and `plan` write the PO's open points as `- [ ] Q<n>: ... → _(open)_ · owner: PO` ledger lines, which are
  the only questions `ado questions --post` sends. Remove and re-add the workflow, and start new runs.
- The extension requires the `ado` CLI 0.2.0 or later (`requires.tools` in `extension.yml`); upgrade the CLI
  before using the 0.2.0 extension.

### Security

- Story text copied into the ledger is escaped so it cannot open a code fence or HTML comment that hides or
  forges later ledger sections.
- Story comments and attachments are untrusted data: `speckit.ado.pull` and the review treat them as context
  only, never as instructions, and keep them in the gitignored `.ado/context/`; author fields are dropped
  and mention anchors become `@user`, but a name typed into comment text stays in that gitignored file.
- The questions comment starts with a visible `[spec-kit-ado:questions]` marker and is edited only when the
  token's own user wrote it.
- The gate and the PR review-freshness checks run git with `--no-replace-objects`, so replace refs and
  grafts cannot change what they see.
- CI annotations that echo untrusted text (CLI stderr, branch names, config paths) are flattened to one
  line and cut at 300 characters; the Azure templates also space a `#` so no `##vso[` command can form.
- Attachment downloads send the token only to the configured organization's Azure DevOps host, accept only
  `AttachedFile` relations with a safe name on `attachments_allow`, keep files under `.ado/attachments/`, and
  stop reading at 5 MB of bytes actually received.
- `ado gate` reads only regular files of at most 1 MiB that resolve under `--src` (test discovery and
  `--changed`): symlinks are never followed, and FIFOs, devices and oversize files are skipped and counted
  in the summary, so a PR cannot use symlinks plus its own `gate.tests` regexes to probe files outside the
  repo. The GitHub gate workflow now posts the PR comment in a second step, so the step that runs `ado gate`
  no longer holds `GH_TOKEN`.
  The ledger `ado gate` reads (its path comes from the PR's config) goes through the same rules, so a
  symlinked or special-file ledger is reported as unreadable instead of read.
- The gate templates (GitHub and Azure) ran `python3 -c` inside the PR checkout, so a PR-committed module with a
  standard-library name (such as `json.py`) ran with the tokens in the environment, and wrote scratch files
  into the checkout, where a PR-committed symlink with the same name was written through: `gate.json`,
  `gate.md` and `changed.bin` in the GitHub template, `gate.json` only in the Azure template (its other
  scratch files were already in `AGENT_TEMPDIRECTORY`). They now run `python3 -I`, keep the scratch files in the runner or agent temp directory
  (`RUNNER_TEMP`, `AGENT_TEMPDIRECTORY`), and the step fails loudly if that variable is unset. Consumers who reference
  the templates at `@v0` get this automatically; consumers who copied them must re-copy them.
- `speckit.ado.pr` no longer puts the story-sourced PR title on a shell line: the interactive preview and apply
  use `ado pr create <id> --from-prepared` with the title written to `.ado/pr-args.json`, like the workflow
  path, so `$(...)` or backticks in a story title cannot run on the developer's machine. A doc test now
  rejects `--title`, `--body` and quoted `"<placeholder>"` values in any command doc's shell block.

## 0.1.1 — 2026-10-27

Hardening release.

### Changed

- `speckit.ado.setup` verifies the organization, project and token against Azure DevOps
  before it stores anything.
- `ado.config.json` is validated when it is loaded, with clear errors, and is read and
  written as UTF-8.
- `--field` requires `Name=Value`; anything else is rejected instead of guessed at.
- The token is looked up once per run, and `ado gate --offline` needs no token.
- Azure Repos PR descriptions are cut to the 4000-character limit, ending with a
  truncation marker (the full text stays in the ledger).
- `ado gate --summary-out <file>` writes the gate comment markdown, so the GitHub and Azure
  CI templates are much shorter. On GitHub the gate keeps one comment and updates it on
  each run instead of adding a new one.
- A gate that could not run now also emits a CI warning annotation (GitHub `::warning::`,
  Azure `##vso[task.logissue type=warning]`) next to the PR comment.
- `gate.tests` regexes and `dod_path` are validated when the config is loaded.
- Prompt fixes: routed commands never receive `--workflow` or other `speckit.ado` control
  flags; `speckit.ado.plan` says when drift was not checked; review findings describe the
  failing input by kind, never by value; `speckit.ado.pr` documents the Azure description cap.

### Removed

- The legacy `dod-gate` command alias and `dod_gate` config key.

### Breaking

- A config that still has a `dod_gate` key now fails with a clear error (exit 2) instead of
  being ignored: move `dod_gate.block` to `gate.block`. The 0.1.0 alias and key are removed
  in this patch release by decision of the maintainer.
- The extension now requires the `ado` CLI 0.1.1 or later (`requires.tools` in
  `extension.yml`); upgrade the CLI before using the 0.1.1 extension.

## 0.1.0 — 2026-10-27

First release.

### Added

- Spec Kit extension `ado` with 12 commands: `speckit.ado.setup`, `speckit.ado.constitution`,
  `speckit.ado.pull`, `speckit.ado.plan`, `speckit.ado.tasks`, `speckit.ado.push`,
  `speckit.ado.implement`, `speckit.ado.gate`, `speckit.ado.review`, `speckit.ado.pr`,
  `speckit.ado.status` and `speckit.ado.done`.
- Two optional hooks: `after_tasks` offers `speckit.ado.push`, `after_implement` offers
  `speckit.ado.gate`.
- `ado` CLI (Python 3.9+, standard library only): `setup`, `get`, `query`, `create`,
  `update`, `comment`, `link`, `attach`, `dod`, `gate` (alias `dod-gate`), `push`,
  `done`, `pr create`, `pr comment`. Batch verbs are dry runs unless `--apply`;
  `--plan-out` writes the preview. The REST client retries only reads (GET and WIQL
  queries) on 429/5xx/network errors, never a create or update; each request times out
  after 30 seconds; network and malformed-response errors exit 1.
- DoD ledger per story (`dod/DoD_<id>.md`) with acceptance criteria copied as verbatim
  text (ADO HTML converted to plain text; table cells, line breaks and numbered lists
  kept), an AC hash for drift detection, and an AC → test → evidence table.
- Gate: AC-to-test traceability, assertion-free test files, AC drift and missing
  evidence, with default test globs for Python, JS/TS, Java/Kotlin, C#, Go and Apex.
  Advisory unless a check is listed in `gate.block`.
- Phase routing to installed stack kits through a confirmed phase map, with core Spec Kit
  as the default.
- HITL contract (`rules/hitl.md`) and routing rules (`rules/routing.md`).
- `ado-lifecycle` workflow with preview → gate → apply for every board or PR change, and
  a bundle that installs the extension and the workflow together. Every gate shows a plan
  file written by an `ado … --plan-out` shell step just before it, including the PR
  preview (`pr-preview`), never by an agent step. Command steps get `--workflow` and make
  no ADO writes; a second push triplet after implement syncs ticked Tasks behind a gate.
  The move to Active is not automated in workflow runs. The workflow ends at the open
  PR; the story closes after merge (CI `close-story`, or `speckit.ado.done` by hand).
- Reusable GitHub Actions workflows and Azure Pipelines templates: gate on PR, branch
  guard, link on merge. Link on merge comments on the story and optionally transitions
  it. Closing is opt-in: with `close-story: true` (`closeStory: true` on Azure
  Pipelines), after merge it attaches the completed ledger and moves the story to
  `done_state`; only if every DoD box is ticked.
- The GitHub reusable workflows delete the toolkit checkout (`.ado-toolkit`) right after
  installing it, so `ado gate --src .` never counts the toolkit's own tests as yours.
- README table of per-agent command names: skill-based agents (Claude Code, Copilot,
  Cursor) use `/speckit-ado-<verb>`; command-file agents use `/speckit.ado.<verb>`.
- Scrub check for secrets and denylisted terms, run in CI.
