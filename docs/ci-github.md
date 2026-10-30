# CI on GitHub Actions

Three reusable workflows in this repo's `.github/workflows/` can be called from a
GitHub repository whose work is tracked in Azure Boards. Reference them at the moving
`v0` tag (see [VERSIONING.md](https://github.com/praveenlavu/spec-kit-ado/blob/main/VERSIONING.md)) or at a release tag such as `v0.2.0`.

| Workflow | Call it on | What it does |
|---|---|---|
| `ado-gate.yml` | `pull_request` | Finds the first `AB#<id>` in the PR title or body, runs `ado gate --base origin/<PR base branch>` on the PR's changed files (so `commits_without_story` checks the PR's own commits), and posts the result as a PR comment. Fails the check when `ado gate` exits 1 with `"blocking": true`, or exits 3 (an invalid `ado.config.json`, e.g. a mistyped `gate.block` name; the comment says `ADO gate FAILED: ado.config.json is invalid: ...`). No `AB#` reference is a notice. A gate that cannot run (any other exit, e.g. 2 for a missing config, or 1 without `"blocking": true`) posts a "could not run" comment and emits a `::warning::` annotation, never a failure. |
| `ado-branch-guard.yml` | `pull_request` | Fails unless `ado branch --id-from` finds a story id in the branch name (`<branch_prefix>ab<id>-<slug>`, e.g. `feature/ab1001-thing`, or `AB#<id>` anywhere; `branch_prefix` is read from `ado.config.json`, default `feature/`) and `ado get` does not answer HTTP 404 for it. Spec Kit's own `001-x` branches carry no id and fail. Missing config or token, or an ADO error other than 404, is a warning. |
| `ado-link-on-merge.yml` | `pull_request: closed` | For a merged PR, comments on every `AB#<id>` in the title or body and optionally sets `transition-state`. With `close-story: true`, after merge it attaches the completed ledger and moves the story to `done_state`; only if every DoD box is ticked. Per-item failures are warnings. |

In strict mode `ado strict check` (L1 order evidence) reads git history: check out with `fetch-depth: 0`, since a shallow clone reports `sha-unknown`. In strict mode `ado done --apply` (the `close-story` step of `ado-link-on-merge.yml`) also runs the `done` strict check: the checkout must hold the merged `<dod_path>/DoD_<id>.md` and `Decisions_<id>.md`; a detached `HEAD` and a shallow clone are enough for it (it needs no red or green commit).

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

All three accept:

| Input | Default | Meaning |
|---|---|---|
| `config-path` | `ado.config.json` | Path to the config in your repo. |
| `toolkit-ref` | `v0` | Ref of `praveenlavu/spec-kit-ado` to install the CLI from. |

`ado-link-on-merge.yml` also takes `transition-state` (default empty: comment only) and
`close-story` (boolean, default `false`). With `close-story: true`, after merge it attaches
the completed ledger and moves the story to `done_state`; only if every DoD box is
ticked. The close runs before `transition-state`; a story the close left in `done_state` is not
transitioned again, and a story that was already closed gets its ledger attached if it is missing.
A story that was already in `done_state` is never transitioned, even if that attach fails or the
ledger is not ready (a warning says the state was left unchanged); this applies when `ado done` runs, i.e. a ledger exists.
The log says which of these happened.

Secrets: `ADO_TOKEN` (required), `TOOLKIT_READ_TOKEN` (optional; falls back to
`github.token`).

## Notes

- To make the gate block a PR, list checks in `gate.block` in `ado.config.json` and mark
  the `gate` job as a required status check. Without `gate.block` it only comments. Valid names are listed in
  [configuration.md](configuration.md); the comment gains an `Open decisions` line when `Decisions_<id>.md` has `D<n>` decision lines.
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
  on the target, or an advisory one, the PR's `ado.config.json` applies as before; the first PR that introduces strict mode is judged by its own config.
  Protect `ado.config.json` on the target branch with branch protection and `CODEOWNERS` (required review from code owners): that file now decides how strict every PR is.
  A non-default `config-path` is not trusted: only the root `ado.config.json` of the target is read, and if it has none the PR's config applies silently.
  The caller workflow and the `toolkit-ref` it passes are PR-controlled too: protect `.github/workflows/` the same way.
  **Not full protection yet:** the shipped workflows' `AB#` step still skips the gate when the PR has no `AB#` reference, so a PR can opt out of it until V3-14
  makes that step fail under a strict target.
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
