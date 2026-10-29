# CI on Azure Pipelines

For Azure Repos, `pipelines/` holds step templates with the same three behaviours as the
GitHub workflows. Full template reference: [pipelines/README.md](https://github.com/praveenlavu/spec-kit-ado/blob/main/pipelines/README.md).

| Template | Run it from | What it does |
|---|---|---|
| `ado-gate.yml` | Branch policy build validation | Runs `ado gate --base origin/<target branch>` on the PR's changed files (so `commits_without_story` checks the PR's own commits) and posts the result as a PR thread with `ado pr comment`. Fails when the gate exits 1 with `"blocking": true`, or when `ado.config.json` is invalid (the story-id step fails the build with the error annotation `ADO gate FAILED: ado.config.json is invalid — <CLI message>`; no PR thread is posted, because `ado pr comment` needs the same config). |
| `ado-branch-guard.yml` | Branch policy build validation | Fails unless `ado branch --id-from` finds a story id in the source branch (`<branch_prefix>ab<id>-<slug>`, e.g. `feature/ab1001-thing`, or `AB#<id>`; `branch_prefix` comes from `ado.config.json`) and `ado get` does not answer HTTP 404 for it. Missing config or token is a warning. |
| `ado-link-on-merge.yml` | CI trigger on the target branch | Comments on each `AB#<n>` in the merge commit message and optionally sets `transitionState`. With `closeStory: true`, after merge it attaches the completed ledger and moves the story to `done_state`; only if every DoD box is ticked. |

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
`repoPath` (default `repo`), and for link-on-merge `transitionState` (default empty) and
`closeStory` (boolean, default `false`). The close runs before `transitionState`; a story the close
left in `done_state` is not transitioned again, and a story that was already closed gets its ledger
attached if it is missing. A story that was already in `done_state` is never transitioned, even if that
attach fails or the ledger is not ready (a warning says the state was left unchanged); this applies when
`ado done` runs, i.e. a ledger exists.

## The PR comment

The thread body is the markdown that `ado gate --summary-out` writes: blocking findings,
advisory findings and AC drift, or "The gate could not run: <reason>" (the token value
redacted). `ado pr comment` adds a new thread on every run; earlier threads are not edited,
so a PR that is rebuilt several times collects one thread per run.

## Agent requirements

Linux agents with bash 4.4 or later. The macOS hosted image ships bash 3.2, so the gate
reports that it could not run there; Windows agents cannot run the scripts.

Whenever the gate could not run (any exit other than 0, or 1 without `"blocking": true`; e.g. a missing config), the
step also logs the warning `spec-kit-ado gate could not run (see the PR comment and job log)`
(`##vso[task.logissue type=warning]`); it never fails the build. An invalid config fails the story-id step instead (see above).

## Trust model

- **The gate trusts the PR's own files.** It reads `ado.config.json` (including
  `gate.block`) and the ledger from the PR's source branch, so a PR can edit them to turn
  blocking off. If blocking matters, protect both with required reviewers for those paths
  in the branch policy.
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
