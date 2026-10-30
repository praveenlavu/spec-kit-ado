# Catalog submissions (DRAFT)

> **DRAFT for the owner to file after publishing.** Do not file these while the repo is
> private. Work through [Go-public checklist](#go-public-checklist) first, then file the three
> submissions in this order: extension, workflow, bundle. Items marked **TO CONFIRM** need a
> value or a check that only exists after publishing.

The extension and the bundle are GitHub issue forms in `github/spec-kit` ("Extension
Submission" and "Bundle Submission"). There is no workflow issue form: a workflow is submitted
as a pull request that adds its entry to `workflows/catalog.community.json`
(`workflows/PUBLISHING.md` upstream). Values come from [extension.yml](../extension.yml),
[workflows/ado-lifecycle/workflow.yml](../workflows/ado-lifecycle/workflow.yml) and
[bundle/bundle.yml](../bundle/bundle.yml).

All three community catalogs are discovery-only, so the bundle cannot install its pinned
components from them. The release therefore carries two install-allowed catalogs,
[catalog/extensions.json](../catalog/extensions.json) and
[catalog/workflows.json](../catalog/workflows.json), served from the tag:

- `https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/catalog/extensions.json`
- `https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/catalog/workflows.json`

## 1. Extension Submission

| Field | Value |
|---|---|
| Issue title | `[Extension]: Add ado` |
| Extension ID | `ado` |
| Extension Name | `Spec Kit for Azure DevOps — human-gated lifecycle` |
| Version | `0.2.0` |
| Description | `Run the full Spec Kit lifecycle from an ADO story back to Done, with human sign-off on every board change.` |
| Author | `praveenlavu` |
| Repository URL | `https://github.com/praveenlavu/spec-kit-ado` |
| Download URL | `https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.2.0.zip` |
| License | `MIT` |
| Homepage | `https://github.com/praveenlavu/spec-kit-ado` |
| Documentation URL | `https://github.com/praveenlavu/spec-kit-ado/blob/main/README.md` |
| Changelog URL | `https://github.com/praveenlavu/spec-kit-ado/blob/main/CHANGELOG.md` |
| Required Spec Kit Version | `>=0.9.0` |
| Number of Commands | `12` |
| Number of Hooks | `2` |
| Tags | `azure-devops, issue-tracking, hitl, workflow, governance` |

**Required Tools**

```markdown
- ado (>=0.2.0) - required; the CLI from this repo: pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.2.0
- python (>=3.9) - required by the ado CLI
- git - required
- gh - optional; needed only to open PRs on GitHub (repo_host: github)
```

**Key Features**

```markdown
- Pulls an Azure DevOps story into a per-story DoD ledger with the acceptance criteria copied as verbatim text (ADO HTML converted to plain text), then routes specify/plan/tasks/implement to the installed stack kit or core Spec Kit
- Mirrors tasks.md into child Tasks on the story; idempotent, dry run by default, applied only after an explicit yes
- Gate: acceptance criterion to named test traceability, assertion-free tests, AC drift, missing evidence; advisory unless a check is opted in to blocking
- Eight-plane review of the branch diff (the AC fidelity plane in strict mode only) with a verification pass, logged in the ledger
- Opens the PR with AB#<id> on GitHub (gh) or Azure Repos, and closes the story with the ledger attached, each after an explicit yes
- Human-in-the-loop contract (rules/hitl.md) linked from every command; optional ado-lifecycle workflow with preview -> gate -> apply for every board change
- Reusable GitHub Actions workflows and Azure Pipelines templates: gate on PR, branch guard, link on merge
```

**Testing Checklist** (tick only after doing each against the published release)

- [ ] Extension installs successfully via download URL
- [ ] All commands execute without errors
- [ ] Documentation is complete and accurate
- [ ] No security vulnerabilities identified
- [ ] Tested on at least one real project

**Submission Requirements**

- [ ] Valid `extension.yml` manifest included
- [ ] README.md with installation and usage instructions
- [ ] LICENSE file included
- [ ] GitHub release created with version tag
- [ ] All command files exist and are properly formatted
- [ ] Extension ID follows naming conventions (lowercase-with-hyphens)

**Testing Details** — **TO CONFIRM** (fill in OS and Spec Kit version actually used)

```markdown
**Tested on:**
- <OS and version> with Spec Kit <version>

**Test project:** a clean Spec Kit project connected to a test Azure DevOps project

**Test scenarios:**
1. Installed the ado CLI with pipx from the v0.2.0 tag
2. Installed the extension with `specify extension add ado --from <download URL>`; 12 commands and 2 hooks registered
3. Ran /speckit.ado.setup and /speckit.ado.pull on a test story
4. Ran ado push and ado done without --apply and confirmed no writes; applied after review
5. Ran the unit test suite (no network): <N> passed
```

**Example Usage**

```bash
# Install the CLI and the extension
pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.2.0
specify extension add ado --from https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.2.0.zip

# Connect, then pull a story
/speckit.ado.setup
/speckit.ado.pull 1001
```

**Proposed Catalog Entry** — **TO CONFIRM** `created_at` / `updated_at` (release date)

```json
{
  "ado": {
    "name": "Spec Kit for Azure DevOps — human-gated lifecycle",
    "id": "ado",
    "description": "Run the full Spec Kit lifecycle from an ADO story back to Done, with human sign-off on every board change.",
    "author": "praveenlavu",
    "version": "0.2.0",
    "download_url": "https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.2.0.zip",
    "repository": "https://github.com/praveenlavu/spec-kit-ado",
    "homepage": "https://github.com/praveenlavu/spec-kit-ado",
    "documentation": "https://github.com/praveenlavu/spec-kit-ado/blob/main/README.md",
    "changelog": "https://github.com/praveenlavu/spec-kit-ado/blob/main/CHANGELOG.md",
    "license": "MIT",
    "requires": {
      "speckit_version": ">=0.9.0",
      "tools": [
        { "name": "ado", "version": ">=0.2.0", "required": true },
        { "name": "git", "required": true },
        { "name": "gh", "required": false }
      ]
    },
    "provides": {
      "commands": 12,
      "hooks": 2
    },
    "tags": ["azure-devops", "issue-tracking", "hitl", "workflow", "governance"],
    "verified": false,
    "downloads": 0,
    "stars": 0,
    "created_at": "<release date>T00:00:00Z",
    "updated_at": "<release date>T00:00:00Z"
  }
}
```

**Additional Context**

```markdown
The ado CLI is Python standard library only and ships in the same repo, at the same version.
The extension manifest cannot carry a workflow: the ado-lifecycle workflow is submitted to the
workflow catalog, and the ado bundle installs both. Two other Azure DevOps extensions exist in the
catalog (azure-devops, maqa-azure-devops) and focus on work-item sync; this one covers the
lifecycle from story to Done with an approval before each board change.
```

## 2. Workflow Submission

A pull request to `github/spec-kit` that adds one entry to `workflows/catalog.community.json`,
keeping the `workflows` object sorted by ID.

| Field | Value |
|---|---|
| Branch | `add-ado-lifecycle-workflow` |
| PR title | `Add ado-lifecycle to community workflow catalog` |
| Workflow ID | `ado-lifecycle` |
| Workflow Name | `ADO story lifecycle` |
| Version | `0.2.0` |
| Repository | `https://github.com/praveenlavu/spec-kit-ado` |
| Workflow URL | `https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/workflows/ado-lifecycle/workflow.yml` |
| Documentation | `https://github.com/praveenlavu/spec-kit-ado/blob/main/workflows/ado-lifecycle/README.md` |

**Catalog entry** — **TO CONFIRM** `created_at` / `updated_at` (release date). `requires`
must equal the `requires` block of `workflow.yml`: a version-pinned install (as `specify bundle
install` does) rejects a downloaded workflow whose requirements differ from the catalog's.

```json
"ado-lifecycle": {
  "id": "ado-lifecycle",
  "name": "ADO story lifecycle",
  "description": "Azure DevOps story to an open PR: spec, plan, tasks, push, implement, gate, review, PR. Every board or PR change is preview, human gate, apply. The story closes after merge.",
  "author": "praveenlavu",
  "version": "0.2.0",
  "url": "https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/workflows/ado-lifecycle/workflow.yml",
  "repository": "https://github.com/praveenlavu/spec-kit-ado",
  "license": "MIT",
  "requires": {
    "speckit_version": ">=0.9.0",
    "integrations": {
      "any": ["alquimia", "claude", "copilot", "gemini", "opencode"]
    }
  },
  "tags": ["azure-devops", "issue-tracking", "hitl", "workflow", "governance"],
  "created_at": "<release date>T00:00:00Z",
  "updated_at": "<release date>T00:00:00Z"
}
```

**Pull request body**

```markdown
## Workflow Submission

**Workflow Name**: ADO story lifecycle
**Workflow ID**: ado-lifecycle
**Version**: 0.2.0
**Repository**: https://github.com/praveenlavu/spec-kit-ado

The workflow lives in workflows/ado-lifecycle/ (workflow.yml, README.md, LICENSE, CHANGELOG.md).
Its shell steps run the ado CLI from the same repository and tag; the README lists them.

### Checklist
- [ ] Valid workflow.yml (passes `specify workflow info`)
- [ ] README.md with description, inputs, and step graph
- [ ] LICENSE file included
- [ ] GitHub release created with raw YAML URL
- [ ] Workflow tested end-to-end with `specify workflow run`
- [ ] All gate steps have clear review messages
- [ ] Input prompts are descriptive
- [ ] Added to workflows/catalog.community.json (alphabetical order)
```

## 3. Bundle Submission

| Field | Value |
|---|---|
| Issue title | `[Bundle]: Add ado` |
| Bundle ID | `ado` |
| Bundle Name | `Spec Kit for Azure DevOps` |
| Version | `0.2.0` |
| Role or Team | `developer` |
| Description | `The ado extension plus the ado-lifecycle workflow: an ADO story to Done with a human gate on every board or PR change.` |
| Author | `praveenlavu` |
| Repository URL | `https://github.com/praveenlavu/spec-kit-ado` |
| Download URL | `https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.2.0/ado-0.2.0.zip` |
| Documentation URL | `https://github.com/praveenlavu/spec-kit-ado/blob/main/bundle/README.md` |
| License | `MIT` |
| Required Spec Kit Version | `>=0.9.0` |
| Integration Target | _(leave empty; integration-agnostic)_ |
| Tags | `azure-devops, issue-tracking, hitl, workflow, governance` |

`ado-0.2.0.zip` is the name `specify bundle build --path bundle` gives the artifact.

**Components Provided**

```markdown
- extensions: ado@0.2.0
- workflows: ado-lifecycle@0.2.0
```

**Required Component Catalogs**

The bundle pins `ado@0.2.0` and `ado-lifecycle@0.2.0`; neither ships with Spec Kit and the
community catalogs are discovery-only, so "None" would fail the submission check. Users add:

```markdown
- Extensions: https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/catalog/extensions.json (add with --install-allowed)
- Workflows: https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/catalog/workflows.json
```

**Key Features**

```markdown
- Installs the ado extension (12 speckit.ado.* commands, 2 optional hooks)
- Installs the ado-lifecycle workflow: spec, plan, tasks, push, implement, gate, review, PR (the story closes after merge)
- Every Azure Boards or PR change in the workflow is a dry-run preview, a gate step that shows the plan file, then an apply step; rejecting any gate aborts the run
```

**Testing Checklist** (tick only after doing each)

- [ ] Validation succeeds with `specify bundle validate --path <bundle-directory>`
- [ ] Build succeeds with `specify bundle build --path <bundle-directory>` and produces the submitted artifact
- [ ] Bundle installs successfully from the built artifact
- [ ] The submitted distribution path was tested end to end, including bundle-ID installation from an install-allowed catalog when a catalog entry is proposed
- [ ] Installation was tested in a clean Spec Kit project
- [ ] Required component catalogs are documented and were included in testing, or no extra catalogs are required
- [ ] Documentation is complete and accurate

**Submission Requirements**

- [ ] Valid `bundle.yml` manifest included
- [ ] README.md explains the bundle's intended role, installed components, and installation steps
- [ ] LICENSE file included
- [ ] GitHub release created with a version tag
- [ ] Bundle ID matches the manifest and follows naming conventions
- [ ] Every extension, preset, workflow, and step reference is pinned where the manifest requires a version

**Testing Details** — **TO CONFIRM** (fill in after testing)

```markdown
**Tested on:**
- <OS and version> with Spec Kit <version>

**Test project:** a clean Spec Kit project connected to a test Azure DevOps project

**Test scenarios:**
1. Added the two required catalogs (extension catalog with --install-allowed, workflow catalog)
2. Validated the bundle manifest (`specify bundle validate --path bundle`)
3. Built ado-0.2.0.zip with specify bundle build
4. Installed the bundle from the artifact in a clean project; the extension and the workflow
   were installed from the two catalogs
5. Installed by bundle ID in another clean project: a temporary install-allowed bundles.json
   (`specify bundle catalog add <url> --id ado-test --policy install-allowed`), then
   `specify bundle install ado`
6. Ran `specify workflow run ado-lifecycle -i story_id=<test story>` and rejected/approved each gate
```

**Example Usage**

```bash
# Install the ado CLI (the workflow's shell steps call it)
pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.2.0

# Add the required component catalogs (the bundle resolves its pinned components from them)
specify extension catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/catalog/extensions.json --name ado --install-allowed
specify workflow catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/catalog/workflows.json --name ado

# Install the bundle artifact (installs the extension and the workflow)
curl -L -o ado-0.2.0.zip https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.2.0/ado-0.2.0.zip
specify bundle install ./ado-0.2.0.zip

# Run the lifecycle for story 1001
specify workflow run ado-lifecycle -i story_id=1001
```

**Proposed Catalog Entry**

```json
{
  "ado": {
    "name": "Spec Kit for Azure DevOps",
    "id": "ado",
    "version": "0.2.0",
    "role": "developer",
    "description": "The ado extension plus the ado-lifecycle workflow: an ADO story to Done with a human gate on every board or PR change.",
    "author": "praveenlavu",
    "license": "MIT",
    "download_url": "https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.2.0/ado-0.2.0.zip",
    "repository": "https://github.com/praveenlavu/spec-kit-ado",
    "requires": {
      "speckit_version": ">=0.9.0"
    },
    "provides": {
      "extensions": 1,
      "presets": 0,
      "steps": 0,
      "workflows": 1
    },
    "tags": ["azure-devops", "issue-tracking", "hitl", "workflow", "governance"],
    "verified": false
  }
}
```

**Additional Context**

```markdown
The workflow's shell steps call the ado CLI, which is not a Spec Kit component; users install
it with pipx from the same tag. The extension (Extension Submission form) and the workflow
(workflow catalog pull request) are submitted separately. The two required catalogs are served
from the v0.2.0 tag of the bundle's repository.

This repository also hosts the extension and the workflow, so the bundle manifest and README live
in bundle/ (bundle/bundle.yml, bundle/README.md); LICENSE is at the repository root.
```

## Go-public checklist

The owner, in this order:

1. Publish from a fresh repository, not by flipping this one: the current history holds a merge commit with a
   non-noreply author (reachable through the PR refs), home paths and internal wording, and the old `v0` / `v0.1.1`
   tags ship `docs/superpowers/`. Recreate `praveenlavu/spec-kit-ado` under the same name (the install URLs keep
   working), with one squashed commit of the final `main` tree:

   ```bash
   # in a scratch clone of the final main (all v0.2 tasks merged), identity set to the GitHub no-reply address
   git checkout --orphan release && git add -A && git commit -m "spec-kit-ado 0.2.0"
   git log --format='%an <%ae>|%cn <%ce>' | sort -u        # must be only the no-reply identity
   python3 scripts/scrub_check.py --require-denylist --history   # exit 0, with the real denylist
   ```

   Then delete the old repository and create the new one (private first), push `release` as `main`, set
   `SCRUB_DENYLIST` (Settings → Secrets → Actions), and re-run the scrub check on the new repository's history.
   Tag the squashed commit `v0.2.0` (annotated; the commit contains `catalog/` and
   `workflows/ado-lifecycle/README.md`). The catalog URLs are pinned to the tag and release tags never move
   ([VERSIONING.md](../VERSIONING.md)), so tag locally, check every file the tag must serve, and abort if one is
   missing; push the tag; and move `v0` to it last, only after the checks and URL tests below pass:

   ```bash
   git tag -a v0.2.0 -m "spec-kit-ado 0.2.0" main
   for f in catalog/extensions.json catalog/workflows.json workflows/ado-lifecycle/README.md \
            workflows/ado-lifecycle/workflow.yml extension.yml bundle/bundle.yml; do
     git cat-file -e "v0.2.0:$f" || { echo "MISSING $f - do not push"; exit 1; }
   done
   git push origin v0.2.0
   ```

   Make the repository public only after the checks pass, then enable private vulnerability reporting, branch
   protection on `main` and secret scanning with push protection, and drop the "while private" text and the
   `TOOLKIT_READ_TOKEN` wording from the CI docs.

2. Build the bundle artifact: `specify bundle build --path bundle` (writes `ado-0.2.0.zip`), and
   check `specify bundle validate --path bundle --offline`.
3. Create the GitHub release `v0.2.0` with `ado-0.2.0.zip` attached.
4. Confirm each URL returns HTTP 200:
   - `https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.2.0.zip`
   - `https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.2.0/ado-0.2.0.zip`
   - `https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/workflows/ado-lifecycle/workflow.yml`
   - `https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/catalog/extensions.json`
   - `https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.2.0/catalog/workflows.json`
5. In a clean Spec Kit project, run the bundle's Example Usage against the real URLs, then
   `specify bundle validate --path <checkout>/bundle` (online) from that project.
6. Test bundle-ID installation (a required checkbox) in another clean project: add the two
   component catalogs, save the bundle's Proposed Catalog Entry under `"bundles"` in a temporary
   `bundles.json` (with `"sha256"` of `ado-0.2.0.zip`), serve it locally
   (`python3 -m http.server 8765 --bind 127.0.0.1`; bundle catalog URLs must be HTTPS or
   localhost), then:

   ```bash
   specify bundle catalog add http://127.0.0.1:8765/bundles.json --id ado-test --policy install-allowed
   specify bundle install ado
   ```

   Fill in the **TO CONFIRM** testing details and dates.
7. File the extension submission, then open the workflow catalog pull request, then file the
   bundle submission.
8. Move `v0` to `v0.2.0` (`git tag -f v0 v0.2.0 && git push -f origin v0`), then test an `@v0` consumer once.
9. Remove the DRAFT banner from this file.

