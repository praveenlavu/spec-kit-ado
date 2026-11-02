# ado bundle

Azure DevOps Boards only (Azure Repos or GitHub for code); Jira and GitHub Issues are not supported.

Installs the `ado` Spec Kit extension (13 `speckit.ado.*` commands) and the `ado-lifecycle`
workflow, which runs an Azure DevOps story from spec to an open pull request.

## What it installs

| Component | Id | Version | What it is |
|---|---|---|---|
| Extension | `ado` | 0.3.0 | The 13 `speckit.ado.*` commands and 2 optional hooks. |
| Workflow | `ado-lifecycle` | 0.3.0 | The story-to-PR run with a human gate before every Azure Boards or PR change. |

It does **not** install the `ado` command-line program (the extension and the workflow's shell steps call it): install that
from the same tag with `pipx`, step 1 below. The bundle, the extension, the workflow and the CLI are released together and
carry the same version; mixing versions is not supported. Spec Kit 1.2.1 or later (the supported floor; nothing below it was tried, see
[docs/compatibility.md](../docs/compatibility.md)) and Python 3.9 or later are required.

**Status: unreleased.** 0.3.0 is the target release. It is not tagged, no `ado-0.3.0.zip` exists on any release, the
repository is private, and there is no bundle catalog entry (no submission has been made). The URLs below start to work
only after the owner publishes the release ([go-public checklist](../docs/catalog-submission.md#go-public-checklist)).
Until then, from a checkout: `specify bundle build --path bundle --output <dir>` writes `ado-<bundle.yml version>.zip`,
which is `ado-0.3.0.zip`. The pinned components come from
the two catalogs below, which do not exist yet, so a build installs only where the extension and the workflow are already
present: install those two first with `specify extension add --dev <path-to-spec-kit-ado>` and
`specify workflow add --dev <path-to-spec-kit-ado>/workflows/ado-lifecycle`.

## Install

1. The `ado` CLI (there is no PyPI package; install from the release tag), then run
   `ado setup` in your own terminal:

   ```bash
   pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.3.0
   ```

2. The two component catalogs. The bundle pins `ado@0.3.0` and `ado-lifecycle@0.3.0`, and
   the Spec Kit community catalogs are discovery-only, so `specify bundle install` resolves
   them from these install-allowed catalogs published with the release:

   ```bash
   specify extension catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/extensions.json --name ado --install-allowed
   specify workflow catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/workflows.json --name ado
   ```

   Adding an install-allowed catalog means anything it lists installs without the
   untrusted-source prompt (`specify workflow catalog add` is always install-allowed). These
   two list only `ado@0.3.0` and `ado-lifecycle@0.3.0` from this repository's `v0.3.0` tag:
   read them and [workflow.yml](https://github.com/praveenlavu/spec-kit-ado/blob/v0.3.0/workflows/ado-lifecycle/workflow.yml) first.

   A project catalog list replaces the built-in one for that project, so `search` then shows
   only these catalogs; `specify extension catalog remove ado` and
   `specify workflow catalog remove 0` restore the defaults.

3. The bundle, from the release artifact; it installs the extension and the workflow:

   ```bash
   curl -L -o ado-0.3.0.zip https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.3.0/ado-0.3.0.zip
   specify bundle install ./ado-0.3.0.zip
   ```

   Without the catalogs, install the components first instead:
   `specify extension add ado --from https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.3.0.zip`
   and `specify workflow add --dev <path-to-spec-kit-ado>/workflows/ado-lifecycle`.

Then run the lifecycle for a story:

```bash
specify workflow run ado-lifecycle -i story_id=1001
```

## Human gates

Each command step runs the agent once with no chance to reply, so approval happens only
at gate steps, and command steps make no ADO writes (the workflow passes them
`--workflow`). Every change to Azure Boards or a pull request is three steps: a shell
preview that writes a plan to `.ado/plan-*.md`, a gate that shows that file, and a shell
step that applies it. The move to Active is not automated: move the story yourself, or
run `speckit.ado.pull` interactively.

| Change | Preview | Gate | Apply |
|---|---|---|---|
| Child Tasks | `ado push <id> --plan-out` | `approve-push` | `ado push <id> --apply` |
| Task states after implement | `ado push <id> --plan-out` | `approve-sync` | `ado push <id> --apply` |
| Pull request | `ado pr create <id> --from-prepared --plan-out` (after `speckit.ado.pr --prepare-only` writes the body) | `approve-pr` | `ado pr create <id> --from-prepared --push --apply` |

The workflow ends at the open PR; it never closes the story. Closing happens after
merge: CI link-on-merge with `close-story: true` (`closeStory: true` on Azure
Pipelines), or `speckit.ado.done` run by hand.

Reject at any gate and the run aborts. Only the numeric `story_id` is interpolated into a
shell command; gate messages are constant text.
