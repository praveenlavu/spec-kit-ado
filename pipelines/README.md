# Azure Pipelines templates

Step templates (`parameters:` + `steps:`) that mirror the GitHub Actions workflows in
`.github/workflows/`. Consume them from an Azure Repos pipeline.

| Template | Runs on | Behaviour |
|---|---|---|
| `ado-link-on-merge.yml` | CI build of the target branch | Comments on each `AB#<n>` in the merge commit; optional `transitionState`. With `closeStory: true`, after merge it attaches the completed ledger and moves the story to `done_state`; only if every DoD box is ticked. Per-item failures are warnings. In strict mode a `transitionState` equal to `done_state` is refused by `ado update` (a warning, the story is not closed): set `closeStory: true`, so `ado done` runs the strict check. |
| `ado-gate.yml` | PR validation build | Runs `ado gate`, posts the result on the PR. Fails on exit 1 **and** `"blocking": true`, or when `ado.config.json` is invalid (error annotation, no PR thread); every other outcome succeeds; a gate that could not run also posts a "could not run" comment and logs a `##vso[task.logissue type=warning]`. |
| `ado-strict-verify.yml` | PR validation build | Optional, secret-free, SHIPPED UNRUN. Does nothing unless the target branch's `ado.config.json` is strict; then it runs `ado strict verify <id> --no-record --base origin/<target>` (the id from the source branch, as for the gate) and fails on any non-zero exit, logging rule ids only. It maps no token, has `persistCredentials: false` and `fetchDepth: 0`, and runs the project's test command on the PR's code: no secret to steal, but a hostile PR can still run arbitrary code on the agent (use ephemeral agents). Plainly: it re-runs the tests at the recorded commits under the target branch's command; it fails while an `l2` or `test-command` override is in force unless `allowOverride` is set; it cannot be satisfied by hand-written records; it does not show a test is meaningful (the PR still writes the tests at those commits). An override in force skips the run, so `ado strict verify --no-record` exits 2 with the finding `override-in-force` (naming the override ids) unless `--allow-override` is passed; the parameter `allowOverride` (default false) adds that flag. It judges the PR head commit (`HEAD_SHA`), as the gate's `--head`. Details: [docs/ci-azure-pipelines.md](https://github.com/praveenlavu/spec-kit-ado/blob/main/docs/ci-azure-pipelines.md). |
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

## Strict mode

With `mode: strict` on the target branch (`ado strict mode --base origin/<target>`), `ado-gate.yml` fails the build when the source branch carries no story id and runs `ado strict check <id> --phase pr` after the gate (rule ids only in the PR thread, no `ADO_TOKEN` for that command); `ado-link-on-merge.yml` fails the build when the strict `ado done` check refuses (that story is not moved by `transitionState`; the others are). A mode that cannot be determined (any exit of `ado strict mode` other than 0, 2 or 3) also fails the gate build. Otherwise both behave as before. Protect the pipeline YAML and `ado.config.json` yourself: see docs/ci-azure-pipelines.md.

## Limitations

- Azure Pipelines has no predefined variable for the PR title or description, so the gate and
  branch guard read the **source branch name** only (`feature/ab1001-thing`, `AB#1001-thing`).
  link-on-merge reads the merge commit message, which Azure Repos fills with the PR title and
  description.
- Untrusted values (branch names, commit messages) reach scripts only through `env:`.

Annotations and log lines that echo CLI stderr (the gate and link-on-merge steps capture `ado`'s stderr to a file and print it only through `ann`) or a branch name (untrusted: it can carry text from the PR's `ado.config.json`) go through an `ann` shell function: one line, control characters replaced by spaces, `#` becomes `# ` so no `##vso[...]` command can form, then a UTF-8-safe cut to 300 characters. The same function (without the `#` rule) protects the `::error::`/`::warning::` lines in `.github/workflows/`. It needs `python3`, which the templates already use.
