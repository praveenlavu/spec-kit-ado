# Catalog submissions (filed record)

> **Filed record: this was the DRAFT for 0.3.0, and all three submissions are now merged.** Extension `ado`: github/spec-kit PR 5348 (now 0.3.2). Workflow `ado-lifecycle`: PR 5312 (0.3.1; the 0.3.2 bump, PR 5345, is open). Bundle `ado`: PR 5335, then PR 5346 (0.3.2, merged).
> The pack below is kept as it was filed at 0.3.0, so its URLs and versions stay at that version; the live catalog entries pin newer tags.
> The Spec Kit catalogs are for discovery only: users still install from this repository, as the [README](../README.md) says.

Azure DevOps Boards only (Azure Repos or GitHub for code); Jira and GitHub Issues are not supported.

**Counts, so the right number goes in the right field.** The catalog entry's `provides.commands` counts the
**extension commands** registered from `extension.yml`: 13 extension commands (`speckit.ado.*`), 2 hooks. The `ado`
command-line program is a separate thing and has 26 top-level CLI verbs (`ado --help`; `ado doctor`, `ado status` and
`ado calibrate` are among them); those are not counted in the catalog entry. A doc test pins both numbers to
`extension.yml` and `ado --help`.

**Unverified in live runs (state these in Testing Details; do not claim them).**

- What another user's edit or delete of a comment does to an approval (only the posting identity's authorship was proven live).
- Comment approvals by a second human identity, end to end (skipped by the owner's decision on 2026-11-03, not attempted): `/approve D1`, `/reject D2` and `/answer Q1` synced and checked live (acceptance criteria 5 and 7),
  and the done-time cross-check case (f) live (criterion 9).
- That Azure DevOps stores comment line breaks as `<br>` (assumed).
- The doctor rows against real restricted tokens (criterion 10 live), including the `bypass` row on a PAT that cannot read the permission.
- GitHub's `base.sha` lag in the close step: an approver removed or added between `base.sha` and the merge.
- The Azure repository's merge-type policy that the one-parent guard needs (Merge no fast-forward or Squash only).
- The CI templates in a consumer repository on a hosted runner, beyond one test: `ado-gate.yml`, `ado-branch-guard.yml` and `ado-strict-verify.yml` ran at `@v0` in a private test repository (2026-11-08, cases a to d); `ado-link-on-merge.yml` and every Azure Pipelines file have never run there, and none has run in a real project.
- The timed fresh-agent quickstart (criterion 1) and the catch-up to a PR (criterion 3).
- The unattended run on a real story, and the keychain read from cron or launchd.

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

- `https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/extensions.json`
- `https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/workflows.json`

## 1. Extension Submission

| Field | Value |
|---|---|
| Issue title | `[Extension]: Add ado` |
| Extension ID | `ado` |
| Extension Name | `Spec Kit for Azure DevOps — human-gated lifecycle` |
| Version | `0.3.0` |
| Description | `Azure DevOps story to Done through Spec Kit, with a human sign-off before every board change.` |
| Author | `praveenlavu` |
| Repository URL | `https://github.com/praveenlavu/spec-kit-ado` |
| Download URL | `https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.3.0.zip` |
| License | `MIT` |
| Homepage | `https://github.com/praveenlavu/spec-kit-ado` |
| Documentation URL | `https://github.com/praveenlavu/spec-kit-ado/blob/v0.3.0/README.md` |
| Changelog URL | `https://github.com/praveenlavu/spec-kit-ado/blob/v0.3.0/CHANGELOG.md` |
| Required Spec Kit Version | `>=1.2.1` |
| Number of Commands | `13` |
| Number of Hooks | `2` |
| Tags | `azure-devops, issue-tracking, hitl, workflow, governance` |

**Required Tools**

```markdown
- ado (>=0.3.0) - required; the CLI from this repo: pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.3.0
- git - required
- gh - optional; needed only to open PRs on GitHub (repo_host: github)
```

(Python >=3.9 is needed by the `ado` CLI; say so under Additional Context, since the manifest does not list it.)

**Key Features** (check each against the final 0.3.0 CHANGELOG before filing)

```markdown
- Azure DevOps Boards only (Azure Repos or GitHub for code); Jira and GitHub Issues are not supported
- Pulls an Azure DevOps story into a per-story DoD ledger with the acceptance criteria copied verbatim, then routes specify/plan/tasks/implement to the installed stack kit or core Spec Kit
- Mirrors tasks.md into child Tasks on the story; dry run by default, applied only after an explicit yes
- DoD gate: acceptance criterion to named-test traceability, assertion-free tests, AC drift, missing evidence, business-rule coverage; advisory unless a check is opted in to blocking
- Strict mode (SDD + TDD): the test command a human approved runs in a throwaway clone, red/green evidence per task, and the optional CI templates (the GitHub ones ran once in a test repository; the Azure Pipelines ones have never run on a hosted runner) re-run the L1 and L2 checks against the target branch; L3 is a model reading and runs locally only
- Stack detection and stack profiles a human approves, with pinned kits (a kit nobody pinned, or one that changed after pinning, is refused in strict mode); never installs a kit itself
- Business rules read from the story's Epic and Feature chain and traced into the ledger
- Adoption and resilience: `ado doctor` prints ok/warn/fail/unknown rows, each with a fix, for git, Spec Kit, config, token, identity, approvers, the bypass-rules permission, stack and kits (the PAT scope probes and `--probe-item` are not built in 0.3.0), `ado status` shows the stage and the next command, with a catch-up view for stories already in flight
- Comment approvals: PO answers, decision verdicts and gate approvals can come as comments from allow-listed identities, with the agent's own comments ignored when it runs as a non-approver identity without the bypass-rules permission, and cross-checked against the story's comments at done; process enforcement, not a security boundary
- Unattended runs: a poller resumes the ado-lifecycle workflow at Spec Kit's native gates (local or persistent machine only; built and unit- and contract-tested, not yet run end to end on a real story)
- Calibration: `ado calibrate` measures a model's AC-fidelity readings against 12 labelled pairs (no model has been scored yet)
- Eight-plane review of the branch diff with a verification pass, logged in the ledger
- Opens the PR with AB#<id> (GitHub or Azure Repos) and closes the story with the ledger attached, each after an explicit yes
- Optional ado-lifecycle workflow (submitted separately) with preview -> gate -> apply for every board or PR change
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
1. Installed the ado CLI with pipx from the v0.3.0 tag
2. Installed the extension with `specify extension add ado --from <download URL>`; 13 commands and 2 hooks registered (confirm the numbers against `specify extension list`)
3. Ran /speckit.ado.setup and /speckit.ado.pull on a test story
4. Ran ado push and ado done without --apply and confirmed no writes; applied after review
5. Ran the unit test suite (no network): <N> passed
```

**Example Usage**

```bash
# Install the CLI and the extension
pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.3.0
specify extension add ado --from https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.3.0.zip

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
    "description": "Azure DevOps story to Done through Spec Kit, with a human sign-off before every board change.",
    "author": "praveenlavu",
    "version": "0.3.0",
    "download_url": "https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.3.0.zip",
    "repository": "https://github.com/praveenlavu/spec-kit-ado",
    "homepage": "https://github.com/praveenlavu/spec-kit-ado",
    "documentation": "https://github.com/praveenlavu/spec-kit-ado/blob/v0.3.0/README.md",
    "changelog": "https://github.com/praveenlavu/spec-kit-ado/blob/v0.3.0/CHANGELOG.md",
    "license": "MIT",
    "requires": {
      "speckit_version": ">=1.2.1",
      "tools": [
        { "name": "ado", "version": ">=0.3.0", "required": true },
        { "name": "git", "required": true },
        { "name": "gh", "required": false }
      ]
    },
    "provides": {
      "commands": 13,
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
The ado CLI is Python standard library only (Python >=3.9) and ships in the same repo, at the same version.
Azure DevOps Boards only (Azure Repos or GitHub for code); Jira and GitHub Issues are not supported.
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
| Version | `0.3.0` |
| Repository | `https://github.com/praveenlavu/spec-kit-ado` |
| Workflow URL | `https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/workflows/ado-lifecycle/workflow.yml` |
| Documentation | `https://github.com/praveenlavu/spec-kit-ado/blob/v0.3.0/workflows/ado-lifecycle/README.md` |

**Catalog entry** — **TO CONFIRM** `created_at` / `updated_at` (release date). `requires`
must equal the `requires` block of `workflow.yml`: a version-pinned install (as `specify bundle
install` does) rejects a downloaded workflow whose requirements differ from the catalog's.

```json
"ado-lifecycle": {
  "id": "ado-lifecycle",
  "name": "ADO story lifecycle",
  "description": "Azure DevOps story to an open PR: spec, plan, tasks, push, implement, gate, review, PR. Every board or PR change is preview, human gate, apply. The story closes after merge.",
  "author": "praveenlavu",
  "version": "0.3.0",
  "url": "https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/workflows/ado-lifecycle/workflow.yml",
  "repository": "https://github.com/praveenlavu/spec-kit-ado",
  "license": "MIT",
  "requires": {
    "speckit_version": ">=1.2.1",
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
**Version**: 0.3.0
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
| Version | `0.3.0` |
| Role or Team | `developer` |
| Description | `The ado extension plus the ado-lifecycle workflow: an ADO story to Done with a human gate on every board or PR change.` |
| Author | `praveenlavu` |
| Repository URL | `https://github.com/praveenlavu/spec-kit-ado` |
| Download URL | `https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.3.0/ado-0.3.0.zip` |
| Documentation URL | `https://github.com/praveenlavu/spec-kit-ado/blob/v0.3.0/bundle/README.md` |
| License | `MIT` |
| Required Spec Kit Version | `>=1.2.1` |
| Integration Target | _(leave empty; integration-agnostic)_ |
| Tags | `azure-devops, issue-tracking, hitl, workflow, governance` |

`ado-0.3.0.zip` is the name `specify bundle build --path bundle` gives the artifact.

**Components Provided**

```markdown
- extensions: ado@0.3.0
- workflows: ado-lifecycle@0.3.0
```

**Required Component Catalogs**

The bundle pins `ado@0.3.0` and `ado-lifecycle@0.3.0`; neither ships with Spec Kit and the
community catalogs are discovery-only, so "None" would fail the submission check. Users add:

```markdown
- Extensions: https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/extensions.json (add with --install-allowed)
- Workflows: https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/workflows.json
```

**Key Features**

```markdown
- Installs the ado extension (13 speckit.ado.* commands, 2 optional hooks)
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
3. Built ado-0.3.0.zip with specify bundle build
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
pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.3.0

# Add the required component catalogs (the bundle resolves its pinned components from them)
specify extension catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/extensions.json --name ado --install-allowed
specify workflow catalog add https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/workflows.json --name ado

# Install the bundle artifact (installs the extension and the workflow)
curl -L -o ado-0.3.0.zip https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.3.0/ado-0.3.0.zip
specify bundle install ./ado-0.3.0.zip

# Run the lifecycle for story 1001
specify workflow run ado-lifecycle -i story_id=1001
```

**Proposed Catalog Entry**

```json
{
  "ado": {
    "name": "Spec Kit for Azure DevOps",
    "id": "ado",
    "version": "0.3.0",
    "role": "developer",
    "description": "The ado extension plus the ado-lifecycle workflow: an ADO story to Done with a human gate on every board or PR change.",
    "author": "praveenlavu",
    "license": "MIT",
    "download_url": "https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.3.0/ado-0.3.0.zip",
    "repository": "https://github.com/praveenlavu/spec-kit-ado",
    "requires": {
      "speckit_version": ">=1.2.1"
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
from the v0.3.0 tag of the bundle's repository.

This repository also hosts the extension and the workflow, so the bundle manifest and README live
in bundle/ (bundle/bundle.yml, bundle/README.md); LICENSE is at the repository root.
```

## Go-public checklist

For **v0.3.0**, the owner, in this order. **Nothing here has been run**: every command below is a plan, and each step that
changes something public (the tag push, making the repo public, the release, the filings) is the owner's explicit decision.
Run everything as the `praveenlavu` identity (`gh auth switch -u praveenlavu` before, switch back after; commits use the
GitHub no-reply address).

0. **Release prep is merged**: the 0.3.0 version bump everywhere including the `catalog/` files and the `@v0.3.0` / `/v0.3.0/` URLs,
   `ado >=0.3.0`, the Spec Kit floor `>=1.2.1`, the description under 100 characters, the tag-pinned documentation links and the
   README's upgrade section. Then date the 0.3.0 headings of both changelogs and take the release candidate (checklist "Before you
   start", item 3). The [release checklist](release-checklist.md) (written, never run: it is run on hosted runners once Actions is
   unlocked, and its steps 1 to 5 come before the commands below).

1. **Publish from a fresh repository, never from the old history.** `praveenlavu/spec-kit-ado` was already recreated as a
   private repository with one squashed commit (the older history is kept out of it on purpose and must not be published). The 0.3 work sits on top of that commit. Check what the flip would publish before anything else:

   ```bash
   git log --format='%an <%ae>|%cn <%ce>' | sort -u                  # only the GitHub no-reply identity
   python3 scripts/scrub_check.py --require-denylist --history        # exit 0, with the real denylist
   ```

   If either fails, stop: do not publish this history; recreate the repository again as one squashed commit of the final
   `main` tree (identity set to the no-reply address) and re-run both checks on it. Confirm the `SCRUB_DENYLIST` Actions
   secret is set on the repository.

2. **Tag locally and check that the tag serves every file.** The catalog URLs are pinned to the tag and release tags never
   move ([VERSIONING.md](../VERSIONING.md)), so tag locally, check every file the tag must serve, and abort if one is missing;
   push the tag only after that, and move `v0` to it last (step 11). The tag goes on `RC`, the release-candidate SHA that the
   [release checklist](release-checklist.md) proved (`RC=$(git rev-parse origin/main)` when that run began), never on whatever
   `main` is now; the check below refuses a tag on any other commit:

   ```bash
   (
     set -e
     git tag -a v0.3.0 -m "spec-kit-ado 0.3.0" "$RC"
     test "$(git rev-parse 'v0.3.0^{commit}')" = "$RC" || { echo "the tag is not the release candidate - do not push"; exit 1; }
     for f in extension.yml bundle/bundle.yml catalog/extensions.json catalog/workflows.json \
              workflows/ado-lifecycle/workflow.yml workflows/ado-lifecycle/README.md README.md LICENSE; do
       git cat-file -e "v0.3.0:$f" || { echo "MISSING $f - do not push"; exit 1; }
     done
     git show v0.3.0:extension.yml | grep -E 'version: "0.3.0"'
     git push origin v0.3.0
   )
   ```

   The block is a subshell, so a refusal (`exit 1`) ends only the subshell and never closes the shell you pasted it into; the push is its last line, so
   it runs only when every check above it passed.

3. **Build the bundle from the tag, not the working tree:**

   ```bash
   tmp=$(mktemp -d) && git worktree add "$tmp/src" v0.3.0
   specify bundle validate --path "$tmp/src/bundle" --offline
   specify bundle build --path "$tmp/src/bundle" --output "$tmp/out"     # writes ado-0.3.0.zip
   (cd "$tmp/out" && shasum -a 256 ado-0.3.0.zip | tee SHA256SUMS)
   (cd "$tmp/out" && shasum -a 256 -c SHA256SUMS)
   ```

4. **Make the repository public** only after steps 1 to 3 pass. Then enable private vulnerability reporting, branch protection
   on `main` and secret scanning with push protection. Then a docs task after the flip: drop the "while private" text and the
   `TOOLKIT_READ_TOKEN` wording from the CI docs, and delete the "Status of the release: nothing is published yet" paragraph in
   the README Quick start, the "Status: unreleased" paragraph in `bundle/README.md` and the "0.3.0 is not tagged yet" paragraph in
   `docs/quickstart.md` section 1 (the install-command doc test pins the
   words "not published", "private" and "no PyPI package", so edit that test in the same commit).

5. **Create the GitHub release** `v0.3.0` with the zip and the checksums attached (the notes are the CHANGELOG 0.3.0 section):

   ```bash
   gh release create v0.3.0 "$tmp/out/ado-0.3.0.zip" "$tmp/out/SHA256SUMS" \
     -R praveenlavu/spec-kit-ado --title "v0.3.0" --notes-file <file holding the CHANGELOG 0.3.0 section> --verify-tag
   ```

   `--verify-tag` aborts the release unless the tag is already on the remote, so GitHub never creates one at the tip of the default branch.

6. **Confirm each URL returns HTTP 200,** and record the extension archive's checksum (`EXT_SHA`, for the catalog entry) and that
   the release asset matches `SHA256SUMS`:

   ```bash
   for u in \
     https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.3.0.zip \
     https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.3.0/ado-0.3.0.zip \
     https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/workflows/ado-lifecycle/workflow.yml \
     https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/extensions.json \
     https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/workflows.json; do
     printf '%s %s\n' "$(curl -sLo /dev/null -w '%{http_code}' "$u")" "$u"; done
   curl -sL https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.3.0.zip | shasum -a 256
   curl -sL https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.3.0/ado-0.3.0.zip | shasum -a 256
   ```

7. **Clean-project tests,** each in a fresh `specify init` project, on the Spec Kit floor you chose and on the latest:
   the extension (`pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.3.0`, then
   `specify extension add ado --from https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.3.0.zip`;
   `specify extension list` shows 13 commands and 2 hooks; run `/speckit.ado.setup` and `/speckit.ado.pull` on a test story);
   the workflow (add the workflow catalog, `specify workflow add ado-lifecycle --version 0.3.0`, `specify workflow info
   ado-lifecycle`, then `specify workflow run ado-lifecycle -i story_id=<test story>`, rejecting one gate and approving through
   to the PR); the bundle from the artifact (the [bundle README](../bundle/README.md) steps verbatim, then `specify bundle list`).

8. **Test bundle-ID installation** (a required checkbox) in another clean project: add the two component catalogs, save the
   bundle's Proposed Catalog Entry under `"bundles"` in a temporary `bundles.json` (with `"sha256"` of `ado-0.3.0.zip`), serve
   it locally (`python3 -m http.server 8765 --bind 127.0.0.1`; bundle catalog URLs must be HTTPS or localhost), then:

   ```bash
   specify bundle catalog add http://127.0.0.1:8765/bundles.json --id ado-test --policy install-allowed
   specify bundle install ado
   specify bundle validate --path "$tmp/src/bundle"      # online, from this project
   ```

9. **Fill in the TO CONFIRM testing details and dates truthfully** (operating system, Spec Kit versions, test counts), ticking only
   the boxes for what was run, including the "tested on a real project" box.

10. **File the extension submission** and wait for its validation to pass (usually 0 to 2 days; fix whatever it reports first).
    Open the workflow catalog pull request (it can run in parallel; check you have fewer than three open pull requests on
    `github/spec-kit`). Then file the bundle submission, after the extension validates.

11. **Move `v0` to `v0.3.0` last** (`git tag -f v0 v0.3.0 && git push -f origin v0`), then test an `@v0` consumer once. This is
    the only tag that moves ([VERSIONING.md](../VERSIONING.md)); if the artifact itself is wrong, cut `v0.3.1` instead of
    moving `v0.3.0`.

12. Add the "listed in the Spec Kit community catalog" line to the README once a listing exists, remove the "nothing is
    published / repository is private" paragraphs (step 4) if not already done, and remove the DRAFT banner from this file.
