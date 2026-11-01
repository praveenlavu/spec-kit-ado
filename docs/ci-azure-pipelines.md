# CI on Azure Pipelines

For Azure Repos, `pipelines/` holds step templates with the same three behaviours (plus an optional fourth, secret-free template) as the
GitHub workflows. Full template reference: [pipelines/README.md](https://github.com/praveenlavu/spec-kit-ado/blob/main/pipelines/README.md).

| Template | Run it from | What it does |
|---|---|---|
| `ado-gate.yml` | Branch policy build validation | Runs `ado gate --base origin/<target branch>` on the PR's changed files (so `commits_without_story` checks the PR's own commits) and posts the result as a PR thread with `ado pr comment`. Fails when the gate exits 1 with `"blocking": true`, or when `ado.config.json` is invalid (the story-id step fails the build with the error annotation `ADO gate FAILED: ado.config.json is invalid — <CLI message>`; no PR thread is posted, because `ado pr comment` needs the same config). The script first runs `ado strict mode --base origin/<target branch>` (local, no token): exit 3 fails the build with that same error annotation, exit 2 (no usable config) is advisory as before, and every other non-zero exit (a crash, 127 for no `ado`) fails the build too: the mode is then unknown. **Under a strict target** a branch with no story id (read by `ado branch --id-from <branch> --base origin/<target branch>`, so with the target's `branch_prefix`) fails the build instead of skipping the gate, and after `ado gate` it runs `ado strict check <id> --phase pr --head $(System.PullRequest.SourceCommitId) --base origin/<target branch>` with `ADO_TOKEN` taken out of that command's environment: its rule ids (never the ledger's text) are appended to the PR thread and the build fails on any non-zero exit. |
| `ado-branch-guard.yml` | Branch policy build validation | Fails unless `ado branch --id-from` finds a story id in the source branch (`<branch_prefix>ab<id>-<slug>`, e.g. `feature/ab1001-thing`, or `AB#<id>`; `branch_prefix` comes from `ado.config.json`) and `ado get` does not answer HTTP 404 for it. Missing config or token is a warning. |
| `ado-strict-verify.yml` | Branch policy build validation | Optional, secret-free strict re-run (see [Optional strict re-run template](#optional-strict-re-run-template-ado-strict-verifyyml-shipped-unrun)). Does nothing unless the target branch's `ado.config.json` is strict; then it runs `ado strict verify <id> --no-record --base origin/<target branch>` and fails the build on any non-zero exit. |
| `ado-link-on-merge.yml` | CI trigger on the target branch | Comments on each `AB#<n>` in the merge commit message and optionally sets `transitionState`. With `closeStory: true`, after merge it attaches the completed ledger and moves the story to `done_state`; only if every DoD box is ticked. A strict `ado done` refusal (exit 2 with findings on stdout) is logged as `##vso[task.logissue type=error]<id>: strict done refused: <rule ids>` (ids only, at most 20, never the findings' text) and fails the build after every story has been handled; a refused story is not moved by `transitionState` either (the other stories are); the checkout is the detached, full-depth merged commit that holds the ledger and `Decisions_<id>.md`. |

## Setup

1. Commit `ado.config.json` with `"repo_host": "azure"` and `"repository"` set to the
   Azure Repos repository name, so the gate can post PR threads. See
   [configuration.md](configuration.md).
2. No PAT is needed: the templates map `$(System.AccessToken)` to `ADO_TOKEN`. Give the
   project's build service identity **Contribute to pull requests** on the repository
   and edit access to work items (comments, state, attachments). Without these the
   templates warn and continue.
3. If you consume the templates from GitHub, create a GitHub service connection for the
   `resources.repositories` entry. While the toolkit repo is private, that connection must
   be able to read it.

## Example

```yaml
# azure-pipelines-pr.yml (used as the branch policy build validation)
trigger: none

resources:
  repositories:
    - repository: spec-kit-ado
      type: github
      endpoint: contoso-github        # your GitHub service connection
      name: praveenlavu/spec-kit-ado
      ref: refs/tags/v0

pool:
  vmImage: ubuntu-latest

# One job per template: each template checks out `self` and the toolkit itself.
jobs:
  - job: branch_guard
    steps:
      - template: pipelines/ado-branch-guard.yml@spec-kit-ado
  - job: gate
    steps:
      - template: pipelines/ado-gate.yml@spec-kit-ado
```

```yaml
# azure-pipelines-merge.yml (CI on the target branch)
trigger:
  branches:
    include: [main]
pr: none

resources:
  repositories:
    - repository: spec-kit-ado
      type: github
      endpoint: contoso-github
      name: praveenlavu/spec-kit-ado
      ref: refs/tags/v0

pool:
  vmImage: ubuntu-latest

steps:
  - template: pipelines/ado-link-on-merge.yml@spec-kit-ado
    parameters:
      transitionState: Resolved      # optional; empty = comment only
      closeStory: false              # true = attach the ledger and move to done_state when the DoD is complete
```

Each template does its own checkouts (`self` into `$(Agent.BuildDirectory)/<repoPath>`,
the toolkit into `$(Agent.BuildDirectory)/spec-kit-ado-toolkit`). Do not add your own
checkout of `self` before them. Parameters: `configPath` (default `ado.config.json`),
`repoPath` (default `repo`), for `ado-strict-verify.yml` `allowOverride` (boolean, default `false`: accept an override in force), and for link-on-merge `transitionState` (default empty) and
`closeStory` (boolean, default `false`). The close runs before `transitionState`; a story the close
left in `done_state` is not transitioned again, and a story that was already closed gets its ledger
attached if it is missing. In strict mode a `transitionState` equal to `done_state` is refused (`ado update --state <done_state>` exits 2 and points to `ado done`; the template logs a warning and the story is not closed): set `closeStory: true`, so `ado done` runs the strict `done` check; without it no strict `done` runs. A story that was already in `done_state` is never transitioned, even if that
attach fails or the ledger is not ready (a warning says the state was left unchanged); this applies when
`ado done` runs, i.e. a ledger exists. If the target branch's `strict.test_command` changes after a squash merge, `done` reports `record-command-mismatch` for the merged story (the record was made with the old command, and `done` cannot re-run the tests). Recovery: re-run `ado strict verify <id>` on the merge result in a checkout of the target branch and commit the ledger, or record an `l2` override (`ado strict override <id> l2 --reason "<text>"`) and post it (`ado decisions <id> --post`).

**End of the flow.** The workflow ends at the open PR; nothing in it ticks ledger section 5, so `ado done` reports the story not ready after the merge until that section is ticked. After the merge run `/speckit-ado-done <id>` (it ticks section 5 only with evidence, shows the plan, then closes the story), or tick section 5 by hand and commit it before CI `closeStory` runs. In strict mode `done` additionally runs the strict `done` check. In strict mode set `closeStory: true`: without it no strict `done` runs.

The test command runs with the PATH of the environment that runs the check (put the toolkit venv first if your tests need it): a Mac has no `python`, so use `python3`. In CI that is the runner's PATH.

## Optional strict re-run template template (`ado-strict-verify.yml`), SHIPPED UNRUN

`pipelines/ado-strict-verify.yml` is the optional strict re-run job, SHIPPED UNRUN. It re-runs the recorded red and green commits in CI: `ado strict verify <id> --no-record --base origin/<PR base branch>` (nothing is written; the exit code is the verdict). It declares no secret, maps no token (no `$(System.AccessToken)`) and checks out with `persistCredentials: false` and `fetchDepth: 0`. Plainly: it re-runs the tests at the recorded commits under the target branch's command; it fails while an `l2` or `test-command` override is in force unless `allowOverride` is set; it cannot be satisfied by hand-written records; it does not show a test is meaningful (the PR still writes the tests at those commits; the fidelity verdicts and the human review gate cover that). An override in force skips the run, so `ado strict verify --no-record` exits 2 with the finding `override-in-force` (naming the override ids) unless `--allow-override` is passed; the parameter `allowOverride` (default false) adds that flag. HEAD = the PR head commit, as the gate's `--head`: the script checks out `HEAD_SHA` (`HEAD_SHA: $(System.PullRequest.SourceCommitId)`, 40 hex characters, else the build fails) with `git checkout --quiet --detach` before any `ado` call, instead of the merge commit PR validation checks out. It is **optional**: add it as its own build-validation job. The step condition is `Build.Reason == PullRequest`.

**Shipped unrun.** Azure Pipelines hosted agents are billing-blocked in this project's environment, so this template has never run on an agent. It is covered by structural pins (no token mapped, `persistCredentials: false`, `fetchDepth: 0`, the exact command, the guards, the `PullRequest` condition), `bash -n` and real-bash tests with a fake `ado` (the same cases as the GitHub workflow, under `bash -e`, `bash -eo pipefail` and `bash --noprofile --norc`). Run it once on a real agent before you make it a required build.

**What it does.** One script: `ado strict mode --base origin/<target branch>` first; only a strict config on **the target branch** lets it go on. With an advisory or absent target config, or a strict config that only the PR carries, it succeeds with a log line and nothing else, because the test command would come from the PR.
Exit 3 and every other unexpected exit fail the build. Then `ado branch --id-from="$branch" --base origin/<target branch>` reads the story id as the gate does (none, under a strict target: the build fails), then `ado strict verify <id> --no-record --base origin/<target branch>` runs the test command from the target branch's config, the runner cloning the PR head into a scrubbed environment.
Any non-zero exit fails the build; the log shows the rule ids (at most 20, `[A-Za-z0-9_-]` only, 40 characters each), never test output.

**Security note: the template runs the project's test command on the PR's code.** It has no secret to steal, but a hostile PR can still run arbitrary code on the runner (the agent): network access and the agent's resources. Use Microsoft-hosted agents or ephemeral self-hosted agents, limit who can open PRs that trigger this build, and do not map any token or variable group into the step.

```yaml
  - job: strict_verify
    steps:
      - template: pipelines/ado-strict-verify.yml@spec-kit-ado
```

## The PR comment

The thread body is the markdown that `ado gate --summary-out` writes: blocking findings,
advisory findings (including `Open decisions` when `Decisions_<id>.md` has `D<n>` decision lines, and a `Business rules` line when `rules.enabled` is true and `rule_coverage` has findings: rule ids, work item ids and counts only, from the checkout's files, with no extra token scope, and a `Rule drift` line from the online `rule_drift` check, see below) and AC drift, or "The gate could not run: <reason>" (the token value
redacted). `ado pr comment` adds a new thread on every run; earlier threads are not edited,
so a PR that is rebuilt several times collects one thread per run.

## Agent requirements

Linux agents with bash 4.4 or later. The macOS hosted image ships bash 3.2, so the gate
reports that it could not run there; Windows agents cannot run the scripts.

Whenever the gate could not run (any exit other than 0, or 1 without `"blocking": true`; e.g. a missing config), the
step also logs the warning `spec-kit-ado gate could not run (see the PR comment and job log)`
(`##vso[task.logissue type=warning]`); it never fails the build. An invalid config fails the story-id step instead (see above).

## Trust model

- **The config comes from the target branch when that is strict.** `ado gate --base origin/<target branch>` reads `ado.config.json` from the PR's
  target branch first. If it says `mode: "strict"`, the whole run uses that config (`gate.block`, `gate.tests`, `dod_path`,
  `branch_prefix`, `review_block_on`, `strict.*`) and the PR thread gains the line `config: target branch (strict)`: the PR's
  own config is ignored for strictness. A target config that is unreadable or invalid, or a git failure, exits 3 and fails the check; a base ref the checkout does not have (use `fetchDepth: 0`) does the same when the PR's own config is strict, and is the v0.2 path (commits not checked, no block) when it is advisory. With no config
  on the target, or an advisory one, the PR's `ado.config.json` applies as before; the first PR that introduces strict mode is judged by its own config and, with `--base`, carries the trust notes (`trust-introducing PR: review ado.stack.json as code`; for a first lockfile `review ado.kits.lock.json as code`).
  Protect `ado.config.json` on the target branch with required reviewers for that path in the branch policy: that file now decides how strict every PR is.
  Protect `ado.stack.json` and `ado.kits.lock.json` on the target branch the same way: under a strict target `gate`, `strict check` and `strict mode` read both from the target commit (each at most 256 KiB), so the target's copies, not the PR's, judge that PR's run: editing them (or `strict.test_command`) does not change the command or the verdict **in that PR's own run**. An unreadable or invalid one on the target fails closed (exit 3 for `gate`, `branch --id-from` and `strict mode`, 2 for `strict check`). The PR's own copies are checked too, because once merged they become the target's: a head profile that is invalid, a draft, changed since approval, deleted, or that does not compose to the head's `strict.test_command` is refused (`stack-profile-*`, detail "at the head commit"). A PR that edits the profile, the lockfile or `strict.test_command` carries `trust-introducing PR: review ado.stack.json as code` in the PR comment and in `warnings`, even when the edit is consistent: once merged it is in force with no further note (an approved hash is computable by anyone), so review edits to these files as code; what runs in CI is the merged `strict.test_command`, which `ado stack approve` sets. After that a new root takes two PRs: the profile first, then the code (a new kit takes two PRs: the lockfile first, then the kit). **Kits, CI against local:** under a strict target `strict check` runs `kit-unpinned` and `kit-changed` (not overridable) against the **target branch's** `ado.kits.lock.json` and the files git **tracks** at the PR head: the digest of each locked kit dir, a newly tracked `.specify/extensions/<dir>/extension.yml` that the target lock does not pin, and a symlink or submodule entry in or in place of a kit dir. It does **not** read `.specify/extensions/.registry`, the agent copies (`.claude/...`), untracked files or `<id>-config.yml`; those are checked by `ado kits verify` on a developer's machine only, and a locked kit with no tracked files is the warning `kit not in the repository: checked locally only`. A PR that deletes or breaks the lockfile is refused; with no lockfile on the target the rules are a note, and the PR that adds the first one carries `trust-introducing PR: review ado.kits.lock.json as code`. See [stack-kits.md](stack-kits.md#kit-rules-local-and-ci). `ado strict verify` does not apply stack or kit rules; they bind at `strict check`. Recovery: a profile or lockfile that is invalid on the target fails every PR, the fix PR included (exit 2 or 3); fix it through an admin-bypass merge.
  The PR pipeline YAML is PR-controlled too: Azure PR validation runs the YAML from the PR's merge commit unless a separate required pipeline or template is configured, so a PR that edits it can change what runs. Protect it the same way.
  **What is protected now:** under a strict target the story-id step reads the id with the target's config (`--base`) and fails (not skips) when it finds none, even if the PR
  edits `branch_prefix` or deletes the config; `ado strict check --phase pr` runs in CI on the PR's source commit and fails the build; and `ado-link-on-merge.yml` fails when the strict
  `done` check refuses. With an advisory or absent target config the templates behave as in v0.2.
  **Still the owner's job:** protect the Azure pipeline YAML (a required build-validation pipeline whose YAML or template lives in a protected repository, and branch policies with required reviewers on the pipeline file), the toolkit repository resource's `ref`, and `ado.config.json`
  on the target branch with required reviewers for those paths. The Azure DevOps agents have not been run in this project's environment: the template logic is covered by `bash -n` and
  real-bash tests with a fake `ado`, not by a hosted run.
- **Recovery from exit 3.** To recover from exit 3 on an invalid target-branch config, fix the target config through an admin-bypass merge, or run the gate once without `--base`. A PR that fixes the config cannot pass the gate on its own, because the gate reads the config of the target branch, which is the invalid one; this holds in an advisory repo too.
- **The ledger is the PR's own file.** It is read from the PR's source branch, so a PR can edit it. If that matters, add required reviewers for `dod/` too.
- **Link-on-merge acts on every `AB#<n>`** in the merge commit message: each one gets the
  comment, `transitionState` and, with `closeStory: true`, the close. Mention other work
  items without the `AB#` prefix.
- **Pin the toolkit code.** Set the toolkit repository resource's `ref` to a release tag
  (for example `refs/tags/v0.2.0`) rather than the moving `refs/tags/v0`, so the code
  that handles the token is fixed and reviewed.
- **Bash.** The scripts need bash 4.4 or later (see Agent requirements). They are
  exercised only in CI, not by this repo's tests.

## Differences from GitHub

- Azure Pipelines has no predefined variable for the PR title or description, so the gate
  and branch guard read the **source branch name** (`feature/ab1001-thing`,
  `AB#1001-thing`). Put the work item ID in the branch name.
- Link-on-merge reads the **merge commit message**, which Azure Repos fills from the PR
  title and description.
- Branch names and commit messages reach the scripts only through `env:`.
- CI helpers run isolated (`python3 -I`) and write only to the agent temp directory, never into the checked-out PR.

## Breaking in 0.2

The branch guard and the gate no longer read "any digit run" in the branch name as a story id, so `feature/1001-thing` and Spec Kit's `001-x` are no longer story 1001 or 1. A branch is tied to a story only as `<branch_prefix>ab<id>-<slug>` (e.g. `feature/ab1001-thing`, the name `ado branch <id>` creates) or by `AB#<id>` anywhere in it. There is no `branch-prefix` input: the prefix is `branch_prefix` in `ado.config.json`, the same value the CLI uses. `AB#` in a PR title, body or merge commit must now be a whole token (`AB#1001`, not `fooAB#1001`) and is case-sensitive. Users of `@v0` should rename branches or add `AB#<id>` to the branch name.

## Business rules: the online drift check

With `rules.enabled`, the gate job also runs `rule_drift`: it compares the committed `Rules_<id>.md` files with the Epic and Feature in ADO (GET requests only, starting from the story id of the branch). **Both CI jobs are required for this check**: make the gate build and the strict verify build (`ado-strict-verify.yml`) required build validations, otherwise a PR can merge without either being run. The build identity behind `$(System.AccessToken)` needs the permission **View work items in this node** on the area path of every Epic and Feature in the chain (that permission is what the build token needs; the PAT scope "Work Items: Read" is for a PAT such as the GitHub `ADO_TOKEN`). Keep **Limit job authorization scope to current project** on (Microsoft recommends it): with it on, the project-scoped build identity may be unable to read an Epic or Feature in another project, which fails with an error (often 404) and is `rules-unreachable` (a blocking `?` in strict mode, a note in advisory mode). Give that identity (`<project> Build Service (<org>)`) read access in the Epic's project, or keep the Epics and Features in the pipeline's project. With `--base` the gate job also lists `rules.reconfirmed` (the ledger's `Rules reviewed:` against the target branch's copy; a note, see [business-rules.md](business-rules.md#the-gate-check)): the target ref must be fetched, so the checkout keeps `fetchDepth: 0`. See [business-rules.md](business-rules.md#drift).
