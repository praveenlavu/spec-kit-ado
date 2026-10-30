# Azure Pipelines templates

Step templates (`parameters:` + `steps:`) that mirror the GitHub Actions workflows in
`.github/workflows/`. Consume them from an Azure Repos pipeline.

| Template | Runs on | Behaviour |
|---|---|---|
| `ado-link-on-merge.yml` | CI build of the target branch | Comments on each `AB#<n>` in the merge commit; optional `transitionState`. With `closeStory: true`, after merge it attaches the completed ledger and moves the story to `done_state`; only if every DoD box is ticked. Per-item failures are warnings. |
| `ado-gate.yml` | PR validation build | Runs `ado gate`, posts the result on the PR. Fails on exit 1 **and** `"blocking": true`, or when `ado.config.json` is invalid (error annotation, no PR thread); every other outcome succeeds; a gate that could not run also posts a "could not run" comment and logs a `##vso[task.logissue type=warning]`. |
| `ado-branch-guard.yml` | PR validation build | Fails unless `ado branch --id-from` finds a story id in the source branch (`feature/ab1001-thing` or `AB#<n>`) that `ado get` does not report as 404. Missing config/token is a warning. |

In strict mode `ado strict check` (L1 order evidence) reads git history: check out with `fetchDepth: 0`, since a shallow clone reports `sha-unknown`.

## Use

```yaml
resources:
  repositories:
    - repository: spec-kit-ado
      type: github                      # or "git" for an Azure Repos mirror
      endpoint: contoso-github          # service connection (GitHub type only)
      name: praveenlavu/spec-kit-ado
      ref: refs/tags/v0

steps:
  - template: pipelines/ado-gate.yml@spec-kit-ado
    parameters:
      configPath: ado.config.json
      repoPath: repo        # where `self` is checked out (default: repo)
```

Run the gate and branch guard from a branch-policy build validation; run link-on-merge
from a CI trigger on the target branch. Each template does its own checkouts, with explicit
paths so nothing depends on implicit multi-repo folder naming:

- `- checkout: self` into `$(Agent.BuildDirectory)/<repoPath>` (`repoPath`, default `repo`;
  your repo is also the working directory of the main step), and
- `- checkout: spec-kit-ado` into `$(Agent.BuildDirectory)/spec-kit-ado-toolkit`, installed
  with `pip install` from there.

Both use `persistCredentials: false`. Python is `UsePythonVersion@0` 3.11. Do not add your own
checkout of `self` before the template.

## Agent requirements

Linux agents with bash >= 4.4 (the gate uses `mapfile -d ''`). The macOS hosted image ships
bash 3.2 (the gate degrades to "could not run"); Windows agents cannot run the scripts.

## Permissions and the token

`ADO_TOKEN` is mapped from `$(System.AccessToken)` on the steps that need it. The project's
build service identity needs Contribute to pull requests (PR comments) and edit access to
work items (comments, state, attachments). Without them the templates warn and carry on.

## Limitations

- Azure Pipelines has no predefined variable for the PR title or description, so the gate and
  branch guard read the **source branch name** only (`feature/ab1001-thing`, `AB#1001-thing`).
  link-on-merge reads the merge commit message, which Azure Repos fills with the PR title and
  description.
- Untrusted values (branch names, commit messages) reach scripts only through `env:`.

Annotations and log lines that echo CLI stderr (the gate and link-on-merge steps capture `ado`'s stderr to a file and print it only through `ann`) or a branch name (untrusted: it can carry text from the PR's `ado.config.json`) go through an `ann` shell function: one line, control characters replaced by spaces, `#` becomes `# ` so no `##vso[...]` command can form, then a UTF-8-safe cut to 300 characters. The same function (without the `#` rule) protects the `::error::`/`::warning::` lines in `.github/workflows/`. It needs `python3`, which the templates already use.
