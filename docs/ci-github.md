# CI on GitHub Actions

Four reusable workflows (the fourth is optional and secret-free) in this repo's `.github/workflows/` can be called from a
GitHub repository whose work is tracked in Azure Boards. Reference them at the moving
`v0` tag (see [VERSIONING.md](https://github.com/praveenlavu/spec-kit-ado/blob/main/VERSIONING.md)) or at a release tag such as `v0.2.0`.

| Workflow | Call it on | What it does |
|---|---|---|
| `ado-gate.yml` | `pull_request` | Finds the first `AB#<id>` in the PR title or body, runs `ado gate --base origin/<PR base branch>` on the PR's changed files (so `commits_without_story` checks the PR's own commits), and posts the result as a PR comment. Fails the check when `ado gate` exits 1 with `"blocking": true`, or exits 3 (an invalid `ado.config.json`, e.g. a mistyped `gate.block` name; the comment says `ADO gate FAILED: ado.config.json is invalid: ...`). No `AB#` reference is a notice. A gate that cannot run (any other exit, e.g. 2 for a missing config, or 1 without `"blocking": true`) posts a "could not run" comment and emits a `::warning::` annotation, never a failure. Before anything else a **Resolve strict mode** step runs `ado strict mode --base origin/<PR base branch>` (local, no token): exit 3 fails the job (`::error::`, fixed text), exit 2 (no usable config) is advisory exactly as before, and every other non-zero exit (a crash, 127 for no `ado`) fails the job too: the mode is then unknown. **Under a strict target** a PR with no `AB#` reference fails the job (instead of the notice) and a **Strict check** step (no `ADO_TOKEN`, no `GH_TOKEN`) runs `ado strict check <id> --phase pr --head <PR head SHA> --base origin/<PR base branch>` after the gate: its rule ids (never the ledger's text) are appended to the PR comment and the last step fails the job on any non-zero exit. |
| `ado-branch-guard.yml` | `pull_request` | Fails unless `ado branch --id-from` finds a story id in the branch name (`<branch_prefix>ab<id>-<slug>`, e.g. `feature/ab1001-thing`, or `AB#<id>` anywhere; `branch_prefix` is read from `ado.config.json`, default `feature/`) and `ado get` does not answer HTTP 404 for it. Spec Kit's own `001-x` branches carry no id and fail. Missing config or token, or an ADO error other than 404, is a warning. |
| `ado-strict-verify.yml` | `pull_request` | Optional, secret-free strict re-run (see [Optional strict re-run job](#optional-strict-re-run-job-ado-strict-verifyyml-shipped-unrun)). Does nothing unless the target branch's `ado.config.json` is strict; then it runs `ado strict verify <id> --no-record --base origin/<PR base branch>` and fails the check on any non-zero exit. |
| `ado-link-on-merge.yml` | `pull_request: closed` | For a merged PR, comments on every `AB#<id>` in the title or body and optionally sets `transition-state`. With `close-story: true`, after merge it attaches the completed ledger and moves the story to `done_state`; only if every DoD box is ticked. Per-item failures are warnings, except a strict `ado done` refusal (exit 2 with findings on stdout): it is annotated `::error::<id>: strict done refused: <rule ids>` (ids only, at most 20, never the findings' text) and fails the job after every story has been handled. A refused story is not moved by `transition-state` either (the other stories are). The workflow checks out the exact merged commit (`ref: ${{ github.event.pull_request.merge_commit_sha }}`, `fetch-depth: 0`, `persist-credentials: false`), a detached checkout that holds the PR's ledger and `Decisions_<id>.md`. |

**End of the flow.** The workflow ends at the open PR; nothing in it ticks ledger section 5, so `ado done` reports the story not ready after the merge until that section is ticked. After the merge run `/speckit-ado-done <id>` (it ticks section 5 only with evidence, shows the plan, then closes the story), or tick section 5 by hand and commit it before CI `close-story` runs. In strict mode `done` additionally runs the strict `done` check. In strict mode set `close-story: true`: without it no strict `done` runs.

In strict mode `ado strict check` (L1 order evidence) reads git history: check out with `fetch-depth: 0`, since a shallow clone reports `sha-unknown`. In strict mode `ado done --apply` (the `close-story` step of `ado-link-on-merge.yml`) also runs the `done` strict check: the checkout must hold the merged `<dod_path>/DoD_<id>.md` and `Decisions_<id>.md`, which the merge-commit checkout does; a detached `HEAD` is enough for it (it needs no red or green commit). If the target branch's `strict.test_command` changes after a squash merge, `done` reports `record-command-mismatch` for the merged story (the record was made with the old command, and `done` cannot re-run the tests). Recovery: re-run `ado strict verify <id>` on the merge result in a checkout of the target branch and commit the ledger, or record an `l2` override (`ado strict override <id> l2 --reason "<text>"`) and post it (`ado decisions <id> --post`).

The test command runs with the PATH of the environment that runs the check (put the toolkit venv first if your tests need it): a Mac has no `python`, so use `python3`. In CI that is the runner's PATH.

## Optional strict re-run job (`ado-strict-verify.yml`), SHIPPED UNRUN

Optional strict re-run job, SHIPPED UNRUN. `ado-strict-verify.yml` re-runs the recorded red and green commits in CI: `ado strict verify <id> --no-record --base origin/<PR base branch>` (nothing is written; the exit code is the verdict). It declares no secret, sets no `ADO_TOKEN`, grants `contents: read` only and checks out with `persist-credentials: false` and `fetch-depth: 0`. Plainly: it re-runs the tests at the recorded commits under the target branch's command; it fails while an `l2` or `test-command` override is in force unless `allow-override` is set; it cannot be satisfied by hand-written records; it does not show a test is meaningful (the PR still writes the tests at those commits; the fidelity verdicts and the human review gate cover that). An override in force skips the run, so `ado strict verify --no-record` exits 2 with the finding `override-in-force` (naming the override ids) unless `--allow-override` is passed; the input `allow-override` (default false) adds that flag. HEAD = the PR head commit, as the gate's `--head` (the first checkout takes `ref: ${{ github.event.pull_request.head.sha }}`, not the merge commit). It is **optional**.

**Shipped unrun.** GitHub Actions is billing-blocked in this project's environment, so this workflow has never run on a hosted runner. It is covered by structural pins
(no secret or token reference, `permissions: contents: read`, the exact command, the guards, `persist-credentials: false` on every checkout, no `pull_request_target`), `bash -n` on every `run:` block and real-bash tests
with a fake `ado` (strict and no story id: fail; strict and exit 0: pass; strict and exit 2: fail with sanitised rule ids only; advisory or absent target: a notice and nothing else; `ado strict mode` exit 3: fail), each under `bash -e`, `bash -eo pipefail` and `bash --noprofile --norc`.
Run it once on a real runner before you make it a required check.

**What it does.** (1) Installs the toolkit as the gate does and removes the checkout. (2) Reads the strict mode with `ado strict mode --base origin/<PR base branch>`: only a strict config on **the target branch** lets it go on; with an advisory or absent target config,
or a strict config that only the PR carries, it succeeds with a notice and does nothing else, because the test command would come from the PR. Exit 3 (invalid config) and every other unexpected exit fail the job, as in the gate. (3) Reads the story id as the gate does (the first whole `AB#<n>` in the PR title or body, passed through `env:`);
under a strict target no `AB#` fails the job. (4) Runs `ado strict verify <id> --no-record --base origin/<PR base branch>`: the test command comes from the target branch's config and the runner clones the PR head into a scrubbed environment. Any non-zero exit fails the job; the log shows the rule ids (at most 20, `[A-Za-z0-9_-]` only, 40 characters each) and never test output or finding text.

**Security note: the job runs the project's test command on the PR's code.** It has no secret to steal (nothing to leak even from a fork), but a hostile PR can still run arbitrary code on the runner: network access and the runner's resources. Call it on `pull_request` only, never `pull_request_target`
(the workflow refuses any other event); keep the repository's setting that requires approval for fork pull requests; use ephemeral (GitHub-hosted or one-shot self-hosted) runners and never a long-lived self-hosted runner with a network position inside your estate.
The toolkit checkout uses the default credential, so the toolkit repository must be readable without `TOOLKIT_READ_TOKEN`; this workflow has no such secret. While `praveenlavu/spec-kit-ado` is private, the cross-repo `uses:` and that token-less toolkit checkout fail: the check goes red and never skips silently (fail closed), so vendor or fork the workflow until the repo is public.

```yaml
  verify:
    if: github.event.action != 'closed'
    uses: praveenlavu/spec-kit-ado/.github/workflows/ado-strict-verify.yml@v0
    permissions:
      contents: read        # no `secrets:` key: it takes none
```

## Breaking in 0.2

The branch guard no longer reads "any digit run" in the branch name as a story id, so `feature/1001-thing` and Spec Kit's `001-x` are no longer story 1001 or 1. A branch is tied to a story only as `<branch_prefix>ab<id>-<slug>` (e.g. `feature/ab1001-thing`, the name `ado branch <id>` creates) or by `AB#<id>` anywhere in it. There is no `branch-prefix` input: the prefix is `branch_prefix` in `ado.config.json`, the same value the CLI uses. `AB#` in a PR title or body (or an Azure merge commit) must now be a whole token (`AB#1001`, not `fooAB#1001`) and is case-sensitive. Users of `@v0` should rename branches or add `AB#<id>` to the branch name.

## Setup

1. Commit `ado.config.json` to the repo (see [configuration.md](configuration.md)).
2. Add a repository secret `ADO_TOKEN`: a PAT, ideally for a service account, with
   **Work Items: Read & Write**.
3. While `praveenlavu/spec-kit-ado` is private, also add `TOOLKIT_READ_TOKEN`, a token
   that can read that repository. Once it is public this secret is not needed.

## Example

```yaml
# .github/workflows/ado.yml
name: ado
on:
  pull_request:
    types: [opened, synchronize, reopened, edited, closed]

jobs:
  gate:
    if: github.event.action != 'closed'
    uses: praveenlavu/spec-kit-ado/.github/workflows/ado-gate.yml@v0
    permissions:
      contents: read
      pull-requests: write
    secrets:
      ADO_TOKEN: ${{ secrets.ADO_TOKEN }}

  branch-guard:
    if: github.event.action != 'closed'
    uses: praveenlavu/spec-kit-ado/.github/workflows/ado-branch-guard.yml@v0
    permissions:
      contents: read
    secrets:
      ADO_TOKEN: ${{ secrets.ADO_TOKEN }}

  link-on-merge:
    if: github.event.action == 'closed' && github.event.pull_request.merged == true
    uses: praveenlavu/spec-kit-ado/.github/workflows/ado-link-on-merge.yml@v0
    permissions:
      contents: read
    with:
      transition-state: Resolved   # optional; empty = comment only
      close-story: false           # true = attach the ledger and move to done_state when the DoD is complete
    secrets:
      ADO_TOKEN: ${{ secrets.ADO_TOKEN }}
```

## Inputs

The first three accept (`ado-strict-verify.yml` takes `toolkit-ref` and `allow-override`, see below):

| Input | Default | Meaning |
|---|---|---|
| `config-path` | `ado.config.json` | Path to the config in your repo. |
| `toolkit-ref` | `v0` | Ref of `praveenlavu/spec-kit-ado` to install the CLI from. |
| `allow-override` | `false` | `ado-strict-verify.yml` only: add `--allow-override` to `ado strict verify`, accepting an override in force (otherwise the job fails with `override-in-force`). |

`ado-link-on-merge.yml` also takes `transition-state` (default empty: comment only) and
`close-story` (boolean, default `false`). With `close-story: true`, after merge it attaches
the completed ledger and moves the story to `done_state`; only if every DoD box is
ticked. The close runs before `transition-state`; a story the close left in `done_state` is not
transitioned again, and a story that was already closed gets its ledger attached if it is missing. In strict mode a `transition-state` equal to `done_state` is refused (`ado update --state <done_state>` exits 2 and points to `ado done`; the template logs a warning and the story is not closed): set `close-story: true`, so `ado done` runs the strict `done` check; without it no strict `done` runs.
A story that was already in `done_state` is never transitioned, even if that attach fails or the
ledger is not ready (a warning says the state was left unchanged); this applies when `ado done` runs, i.e. a ledger exists.
The log says which of these happened.

Secrets: `ADO_TOKEN` (required), `TOOLKIT_READ_TOKEN` (optional; falls back to
`github.token`).

## Notes

- To make the gate block a PR, list checks in `gate.block` in `ado.config.json` and mark
  the `gate` job as a required status check. Without `gate.block` it only comments. Valid names are listed in
  [configuration.md](configuration.md); the comment gains an `Open decisions` line when `Decisions_<id>.md` has `D<n>` decision lines, and a `Business rules` line when `rules.enabled` is true and the `rule_coverage` check has findings (rule ids, work item ids and counts only; local, so it needs no extra token scope), and a `Rule drift` line when the online `rule_drift` check has findings (see below). With `--base` the target branch's strict config decides whether rules are enabled, so a PR cannot switch the check off.
- PR titles, bodies and branch names reach the scripts only through environment
  variables, never interpolated into the script text.
- The PR comment is the markdown that `ado gate --summary-out gate.md` writes: blocking
  findings, advisory findings and AC drift, or "The gate could not run: <reason>" (the
  `ADO_TOKEN` value redacted). Each run edits the newest comment by `github-actions[bot]` that starts with
  `<!-- spec-kit-ado:gate -->`, so it never overwrites another workflow's comment, and posts a new one when there is none. If the CLI
  crashes before writing the file, the comment says the gate could not run (see the job log). Whenever the gate could not run, the step also emits the warning annotation `spec-kit-ado gate could not run (see the PR comment and job log)`.

## Trust model

- **The config comes from the target branch when that is strict.** `ado gate --base origin/<PR base branch>` reads `ado.config.json` from the PR's
  target branch first. If it says `mode: "strict"`, the whole run uses that config (`gate.block`, `gate.tests`, `dod_path`,
  `branch_prefix`, `review_block_on`, `strict.*`) and the PR comment gains the line `config: target branch (strict)`: the PR's
  own config is ignored for strictness. A target config that is unreadable or invalid, or a git failure, exits 3 and fails the check; a base ref the checkout does not have (use `fetch-depth: 0`) does the same when the PR's own config is strict, and is the v0.2 path (commits not checked, no block) when it is advisory. With no config
  on the target, or an advisory one, the PR's `ado.config.json` applies as before; the first PR that introduces strict mode is judged by its own config and, with `--base`, carries the trust notes (`trust-introducing PR: review ado.stack.json as code`; for a first lockfile `review ado.kits.lock.json as code`).
  Protect `ado.config.json` on the target branch with branch protection and `CODEOWNERS` (required review from code owners): that file now decides how strict every PR is.
  Protect `ado.stack.json` and `ado.kits.lock.json` on the target branch the same way: under a strict target `gate`, `strict check` and `strict mode` read both from the target commit (each at most 256 KiB), so the target's copies, not the PR's, judge that PR's run: editing them (or `strict.test_command`) does not change the command or the verdict **in that PR's own run**. An unreadable or invalid one on the target fails closed (exit 3 for `gate`, `branch --id-from` and `strict mode`, 2 for `strict check`). The PR's own copies are checked too, because once merged they become the target's: a head profile that is invalid, a draft, changed since approval, deleted, or that does not compose to the head's `strict.test_command` is refused (`stack-profile-*`, detail "at the head commit"). A PR that edits the profile, the lockfile or `strict.test_command` carries `trust-introducing PR: review ado.stack.json as code` in the PR comment and in `warnings`, even when the edit is consistent: once merged it is in force with no further note (an approved hash is computable by anyone), so review edits to these files as code; what runs in CI is the merged `strict.test_command`, which `ado stack approve` sets. After that a new root takes two PRs: the profile first, then the code (a new kit takes two PRs: the lockfile first, then the kit). **Kits, CI against local:** under a strict target `strict check` runs `kit-unpinned` and `kit-changed` (not overridable) against the **target branch's** `ado.kits.lock.json` and the files git **tracks** at the PR head: the digest of each locked kit dir, a newly tracked `.specify/extensions/<dir>/extension.yml` that the target lock does not pin, and a symlink or submodule entry in or in place of a kit dir. It does **not** read `.specify/extensions/.registry`, the agent copies (`.claude/...`), untracked files or `<id>-config.yml`; those are checked by `ado kits verify` on a developer's machine only, and a locked kit with no tracked files is the warning `kit not in the repository: checked locally only`. A PR that deletes or breaks the lockfile is refused; with no lockfile on the target the rules are a note, and the PR that adds the first one carries `trust-introducing PR: review ado.kits.lock.json as code`. See [stack-kits.md](stack-kits.md#kit-rules-local-and-ci). `ado strict verify` does not apply stack or kit rules; they bind at `strict check`. Recovery: a profile or lockfile that is invalid on the target fails every PR, the fix PR included (exit 2 or 3); fix it through an admin-bypass merge.
  A non-default `config-path` is not trusted: only the root `ado.config.json` of the target is read, and if it has none the PR's config applies silently.
  The caller workflow and the `toolkit-ref` it passes are PR-controlled too: protect `.github/workflows/` the same way.
  **What is protected now:** under a strict target the shipped workflows fail (not skip) when the PR has no `AB#` reference, run `ado strict check --phase pr`
  in CI on the PR's head SHA and fail on it, and `ado-link-on-merge.yml` fails when the strict `done` check refuses. So the story-id fail-closed, the strict check in CI and the strict
  `done` at merge are all in place. With an advisory or absent target config the workflows behave as in v0.2.
  **Still the owner's job:** protect `.github/workflows/*`, the caller workflow and the `toolkit-ref` it passes (pin it to a tag or commit SHA), and `ado.config.json` on the target
  branch with branch rules or `CODEOWNERS`: a PR that edits the caller workflow or the config can still change what runs. The GitHub Actions agents have not been run in this
  project's environment (billing): the workflow logic is covered by `bash -n` and real-bash tests with a fake `ado`, not by a hosted run.
- **Recovery from exit 3.** To recover from exit 3 on an invalid target-branch config, fix the target config through an admin-bypass merge, or run the gate once without `--base`. A PR that fixes the config cannot pass the gate on its own, because the gate reads the config of the target branch, which is the invalid one; this holds in an advisory repo too.
- **The ledger is the PR's own file.** It is read from the PR's checkout, so a PR can edit it. If that matters, protect `dod/` with `CODEOWNERS` too.
- **Link-on-merge acts on every `AB#<n>`** in the PR title and body: each one gets the
  comment, `transition-state` and, with `close-story: true`, the close. Mention other work
  items without the `AB#` prefix.
- **Pin the toolkit code.** Pinning `uses: …@v0.2.0` does not pin the CLI the workflows
  install: that comes from `toolkit-ref` (default: the moving `v0` tag). Set
  `toolkit-ref` to a release tag or commit SHA so the code that handles `ADO_TOKEN` is
  fixed and reviewed.
- **Isolated helpers.** CI helpers run isolated (`python3 -I`) and write only to the runner temp directory, never into the checked-out PR.
- **Bash.** The scripts need bash 4.4 or later (`ubuntu-latest` has it). They are
  exercised only in CI, not by this repo's tests.

## Business rules: the online drift check

With `rules.enabled`, the `gate` job also runs `rule_drift`: it compares the committed `Rules_<id>.md` files with the Epic and Feature in ADO (GET requests only, starting from the story id of the PR). **Both CI jobs are required for this check**: make the `gate` job (it holds `ADO_TOKEN`) and the strict re-run job (`ado-strict-verify.yml`) required status checks, otherwise a PR can merge without either being run. `ADO_TOKEN` needs **Work Items: Read** on the project of every Epic and Feature in the chain; when it cannot read one, strict mode reports `rules-unreachable` (a blocking `?`) and advisory mode a note. See [business-rules.md](business-rules.md#drift).
