# Release checklist (0.3.0 and later)

> **Status: NOT RUN.** Nothing in this file has been executed. The CI in this repository has **never run on a hosted runner** (Actions billing for
> the account is blocked), so every workflow and pipeline template here is covered only by structural tests, `bash -n` and real-bash tests with a
> fake `ado`. The commands below are a plan. Task P4-21 runs this checklist, in order, against a release-candidate SHA once the owner unlocks CI,
> and records expected against actual in the [record sheet](#record-sheet). Until then, do not write "tested on a hosted runner" anywhere.

The checklist follows the design's section 4.8 and acceptance criterion 14: the release is ready when every step has been run end to end and every
result is recorded. It has two helpers in this repository: [scripts/ci_smoke/](../scripts/ci_smoke/) (the six consumer cases as fixtures, with a local
runner) and [catalog-submission.md](catalog-submission.md) (the filings that come after the release).

**Who does what.** Every step has a **Who** line.

- **Owner**: only the repository owner does this, as himself, in his own terminal or browser. Owner-only: the repository visibility flip, every
  catalog filing (made as his account on a repository he does not control), creating a PAT and setting a secret with it (`ADO_TOKEN`,
  `TOOLKIT_READ_TOKEN`), granting and removing the `delete_repo` scope on his `gh` token, the Actions access setting of the toolkit repository, and
  every push of a release tag. The controller never does these, and an agent never types a token or passes one in argv.
- **Controller**: the person or session running the release may run it. It reads state, runs local checks, or creates things that can be deleted
  again (a private consumer repository, pull requests in it, a tag that has not been pushed).

Run everything that uses `gh` as the repository owner's identity (`gh auth switch -u praveenlavu` before, switch back after). `RC` below is the
release-candidate SHA: `RC=$(git rev-parse origin/main)` after the release-prep commits are on `main`, and it does not change during the run. If
`main` moves, start again from step 1.

## Before you start

All of these must be true. If one is not, stop: the later steps would prove nothing.

1. **CI billing is unlocked and Actions is enabled** on `praveenlavu/spec-kit-ado`. Check that a push starts a hosted run:
   `gh run list -R praveenlavu/spec-kit-ado --limit 3` shows a run for the latest push that is `queued`, `in_progress` or `completed` (not
   "startup failure"). **Owner** (billing is an account setting).
2. **The Python matrix exists in `ci.yml`**: its `test` job runs on Python 3.9 (the supported floor, `requires-python >=3.9`) and 3.13, on
   `ubuntu-24.04`. The 3.9 leg does not install the Spec Kit CLI (Spec Kit needs Python 3.11 or later), so the real-repository tests skip there and
   run on the 3.13 leg. Both legs check out the full history and set `ADO_REQUIRE_BASELINE=1`, so a missing baseline commit makes the byte-identity
   tests fail instead of skipping: **a green leg means they ran**. Step 1 is what shows both legs green on a hosted runner; nothing is tagged
   before that. The job installs the package editable (the tests run the checkout) and ends with a step that builds the wheel and checks it
   carries the seeds and the golden set; the real-repository fixture passes `--ignore-agent-tools` to `specify init` because Spec Kit 1.2.1 wants the
   `claude` CLI on PATH. To lint the workflows locally as the `actionlint` job does, put `shellcheck` and the pinned `actionlint` 1.7.7 on PATH.
3. **The release-prep task has merged**: version 0.3.0 everywhere and the catalog files. This checklist does not do that.
   `git show origin/main:extension.yml | grep 'version: "0.3.0"'` prints the line. **Date the 0.3.0 headings** before you pick `RC`:
   `CHANGELOG.md` and `workflows/ado-lifecycle/CHANGELOG.md` say `## 0.3.0 — unreleased`; replace `unreleased` with the release date
   (`## 0.3.0 — YYYY-MM-DD`) and set `updated_at` in `catalog/extensions.json` and `catalog/workflows.json` (the top level and the entry in each,
   four values) to the same date (`YYYY-MM-DDT00:00:00Z`); commit that to `main`, and only then take `RC` (the date is part of the release candidate;
   the tests accept both the `unreleased` and the dated heading).
4. **The contract canary** (`speckit-contract.yml`) has a green run on the minimum and the latest Spec Kit for this `RC`. Step 2 does that run.
5. **The go-public audit** is done, and the scrub check passes with the real denylist on the full history:
   `python3 scripts/scrub_check.py --require-denylist --history` exits 0. The denylist is never printed or pasted anywhere.
   `git log --format='%an <%ae>|%cn <%ce>' | sort -u` shows only the GitHub no-reply identity. **Owner** (the denylist is his).
6. **The local rehearsal passes**: `python3 scripts/ci_smoke/run_local.py` (from the checkout, any `python3`: it puts `src/` on the path itself) prints `ok` for cases (a) to (f) and exits 0. This shows the checks
   give the expected answer on the fixtures; it does not show how a hosted runner behaves.
7. **A smoke Azure DevOps project** (`spec-kit-ado-smoke`) has a story for each case, two PATs the owner creates (an Azure DevOps PAT with Work Items:
   Read & Write, and a fine-grained GitHub PAT with Contents: Read-only on `praveenlavu/spec-kit-ado` alone, short expiry; see step 3), and, for case
   (e), approvers configured in the consumer's `main` (see
   [configuration.md](configuration.md#approvers-and-comment-mode-the-recommended-setup)). **Owner** (PAT creation).
   **Whose Azure DevOps PAT it is matters.** CI's `ADO_TOKEN` is the PAT of a service or agent identity (a Contributor), **not** the owner's own account: it must not
   hold "Bypass rules on work item updates" (organization and project administrators usually do), and it must not be one of the `approvers`. With a bypass-capable token
   the cross-check answers `decisions-xcheck-unreachable` for cases (e) and (f); with an approver's own token that approver's `/approve` is ignored
   (`decisions-xcheck-verdict`). The second human who posts the `/approve` comments cannot be that identity either. Before step 3, run `ado doctor` with that PAT: its
   `bypass` row must be `ok` (or read the identity's permission page; whether the owner's own identity holds the permission is not known).

## Step 1: `ci` on `main` (tests, full-history scrub, actionlint, Python 3.9 and 3.13)

**Who:** Controller (reads the result). The push that starts the run is the owner's normal push of `main`.

**Run:**

```bash
RC=$(git rev-parse origin/main)
gh run list -R praveenlavu/spec-kit-ado --workflow ci --branch main --event push --commit "$RC" --json databaseId,conclusion,status
RUN=<databaseId from the line above>
gh run view "$RUN" -R praveenlavu/spec-kit-ado --json jobs --jq '.jobs[] | [.name, .conclusion] | @tsv'
```

**Expected:** one run for `RC` with `conclusion: success`, and exactly these jobs, each `success`: `test (3.9)`, `test (3.13)`, `scrub`, `actionlint`.
The `scrub` job on a push to `main` runs `--require-denylist --history` (the `SCRUB_DENYLIST` repository secret must be set). No job is `skipped`
except the steps that `ci.yml` skips by design on the 3.9 leg. If the run did not start or is still going, wait; do not continue on a partial result.
Both `test` legs ran on a full-history checkout (the byte-identity tests against the baseline commits `a0b3c9e` and `f83c868` need it): if a leg is
green with a depth-1 checkout in its log, treat that as a defect, not a pass.

**Rollback:** nothing was changed. If a leg is red, read its log, fix forward through a normal pull request, and restart from step 1 with the new
`RC`. `gh run rerun "$RUN" -R praveenlavu/spec-kit-ado --failed` is for a run that failed for a reason outside the code (a runner outage), only after
the log was read.

## Step 2: the contract job on the minimum and the latest Spec Kit

**Who:** Controller.

**Run:**

```bash
gh workflow run speckit-contract.yml -R praveenlavu/spec-kit-ado --ref main
gh run list -R praveenlavu/spec-kit-ado --workflow speckit-contract --event workflow_dispatch --limit 1 --json databaseId,headSha,createdAt,status,conclusion
gh run watch <databaseId> -R praveenlavu/spec-kit-ado --exit-status
gh run view <databaseId> -R praveenlavu/spec-kit-ado --json jobs --jq '.jobs[] | [.name, .conclusion] | @tsv'
```

**Expected:** the run listed is the one just started (its `headSha` equals `RC` and its `createdAt` is now, not an older run), with two jobs whose names start with `contract (`, one for the minimum (upstream commit `9dca9bf42a031765ee5e5f2371137f2f26967e11`, v1.2.1)
and one for the latest release tag, both `success`, and the step "Fail if any contract test was skipped" passes. A red run here means "read
[compatibility.md](compatibility.md)", not "the code is wrong": decide whether the floor moves or the kit adapts, then restart from step 1.

**Rollback:** nothing was changed (the workflow is read-only and holds no secret).

## Step 3: a private GitHub consumer repository, one pull request per case (a) to (f)

**Who:** Owner for creating the two PATs and setting them as secrets, for the Actions access setting of the toolkit repository, and for the `delete_repo`
scope. Controller for everything else.

The consumer repository is private and holds only fictional data. It calls the reusable workflows **pinned to `RC`** (not `@v0`: `v0` moves last,
in step 6), with `toolkit-ref: <RC>` where an input exists. Each case below is a branch and a pull request built from the matching fixture in
[scripts/ci_smoke/](../scripts/ci_smoke/) (`cases.json` names the files and the local runner builds the same repository in a temporary directory; for
the hosted run the fixture's story id `1001` is replaced by the smoke story's id in the ledger, the commit messages and the PR title).

**Prepare (once):**

```bash
gh repo create praveenlavu/ci-smoke-consumer --private --clone
# Owner: lets the private consumer call workflows of the private toolkit repository. The access level `user` applies only to repositories owned by the same
# user, which is why the consumer lives under praveenlavu; the REST endpoint is PUT /repos/{owner}/{repo}/actions/permissions/access, and the setting shows
# in Settings > Actions > General > Access as "Accessible from repositories owned by 'praveenlavu' user". First write down the value it has now
# (`none`, `user` or `organization`): the rollback restores THAT value, not a guess.
gh api repos/praveenlavu/spec-kit-ado/actions/permissions/access --jq .access_level
gh api -X PUT repos/praveenlavu/spec-kit-ado/actions/permissions/access -f access_level=user
# Owner: both PATs are created in the owner's browser (Azure DevOps: Work Items Read & Write, for the service identity of prerequisite 7, never the owner's own account; GitHub: a fine-grained PAT with Contents Read-only on
# praveenlavu/spec-kit-ado alone, short expiry). `gh secret set` prompts for the value, so it is typed by the owner in his own terminal: never
# in argv (no --body), never printed, never in chat.
gh secret set ADO_TOKEN -R praveenlavu/ci-smoke-consumer
gh secret set TOOLKIT_READ_TOKEN --repo praveenlavu/ci-smoke-consumer
```

Why `TOOLKIT_READ_TOKEN`: the access setting only lets the consumer *use* the reusable workflows. Their toolkit checkout step reads the private toolkit
repository with `secrets.TOOLKIT_READ_TOKEN || github.token` ([ci-github.md](ci-github.md#setup), Setup 3), and `github.token` of the consumer cannot read
it. Without the secret every gate, branch-guard and link-on-merge job fails at that checkout, and the cases that are *expected* to fail would fail for
the wrong reason. Commit `.github/workflows/ado.yml` as in [ci-github.md](ci-github.md#example) with `uses: ...@<RC>`, `toolkit-ref: <RC>`, and
`TOOLKIT_READ_TOKEN: ${{ secrets.TOOLKIT_READ_TOKEN }}` next to `ADO_TOKEN` under `secrets:` for the gate, branch-guard and link-on-merge jobs, plus the
`ado.config.json` of the case.

**Strict-verify while the toolkit is private.** `ado-strict-verify.yml` declares no secret and checks the toolkit out with the default token, so called
across repositories it cannot read the private toolkit. To run its first-ever hosted run before anything is public, vendor a copy of the file at `RC`
into the consumer, with exactly two edits (a secret declaration and a token line on the toolkit checkout), and call it as a local reusable workflow.
In a clone of the consumer, with the toolkit clone at `$TOOLKIT`:

```bash
git -C "$TOOLKIT" show "$RC:.github/workflows/ado-strict-verify.yml" > .github/workflows/ado-strict-verify.yml
python3 - <<'PY'
import pathlib
p = pathlib.Path(".github/workflows/ado-strict-verify.yml")
t = p.read_text()
secret_a = "        default: false\n\npermissions:"
secret_b = "        default: false\n    secrets:\n      TOOLKIT_READ_TOKEN:\n        required: false\n\npermissions:"
token_a = "          path: .ado-toolkit\n"
token_b = token_a + "          token: ${{ secrets.TOOLKIT_READ_TOKEN || github.token }}\n"
assert t.count(secret_a) == 1 and t.count(token_a) == 1, "the template changed: edit this step"
p.write_text(t.replace(secret_a, secret_b).replace(token_a, token_b))
PY
git -C "$TOOLKIT" show "$RC:.github/workflows/ado-strict-verify.yml" | diff - .github/workflows/ado-strict-verify.yml
```

The `diff` must show exactly those two additions and nothing else; record it. Then add to the consumer's `ado.yml` a `strict-verify` job with
`uses: ./.github/workflows/ado-strict-verify.yml`, `with: toolkit-ref: <RC>`, `permissions: contents: read` and `secrets: TOOLKIT_READ_TOKEN: ${{ secrets.TOOLKIT_READ_TOKEN }}`.
This proves the template's logic on a hosted runner while the repository is private; it does **not** prove the cross-repository `uses:` of the shipped file,
which step 6.5 re-runs after the flip.

**Run, per case** (open each as a separate pull request from its own branch; `gh pr checks --watch` waits for the result):

```bash
gh pr create -R praveenlavu/ci-smoke-consumer --head <branch> --base main --title "<title> AB#<story>" --body "ci smoke case (<letter>)"
gh pr checks <number> -R praveenlavu/ci-smoke-consumer --watch
gh pr view <number> -R praveenlavu/ci-smoke-consumer --json statusCheckRollup --jq '.statusCheckRollup[] | [.name, .conclusion] | @tsv'
gh run list -R praveenlavu/ci-smoke-consumer --branch <branch> --limit 1 --json databaseId
gh run view <databaseId> -R praveenlavu/ci-smoke-consumer --log | grep -E '<pattern from the last column>'
```

| Case | What the pull request is | Local result (`run_local.py`) | Expected on the hosted runner | The log must match |
|---|---|---|---|---|
| (a) | An advisory project; the branch name carries no story id (`feature/members-list`). | `ado branch --id-from` exits 1; the gate exits 0, not blocking. | `branch-guard` **fails** with the guard's error; `gate` stays green (advisory) and its comment lists the commits without the story. | `##\[error\]Branch .* is not tied to a work item` |
| (b) | The PR's own `ado.config.json` says advisory; the **target** branch is strict. | `ado strict mode --base main` prints `strict` from the `target branch`; the strict check exits 2 with `stack-profile-drift`. | `gate` runs in strict mode from the target's config and **fails**. The PR cannot weaken its own strictness. | `stack-profile-drift` |
| (c) | A story with valid strict evidence: a red commit, a green commit, a verification record, a review and an AC fidelity block. | `gate`, `strict check --phase pr` and `strict verify --no-record` all exit 0, in strict mode from the target branch. | `gate` green; the vendored `strict-verify` green (shipped file: step 6.5). | `spec-kit-ado: strict verify passed\.$` |
| (d) | The ledger names a "green" commit at which the tests still fail. | `strict verify --no-record` exits 2 with `green-fails`. | the vendored `strict-verify` **fails** (shipped file: step 6.5). | `green-fails` |
| (e) | The story's PR merges, with approvers configured on the target branch and a decisions file backed by approver comments. | `ado done` (preview) exits 0 with `ready: true` and no cross-check warning. | After the merge, `link-on-merge` (with `close-story: true`) moves the story to the done state (the `ADO_TOKEN` is the service identity of prerequisite 7: with a bypass-capable or an approver's token this case is red for the wrong reason, `decisions-xcheck-unreachable` or `decisions-xcheck-verdict`, and counts as a mismatch). | present: `[0-9]+: DoD complete - ledger (was already )?attached, story closed\.$`; absent: `##\[warning\][0-9]+: done cross-check` |
| (f) | The decisions file carries a `posted:` marker that does not name a posting of the kit (the fixture names a comment that does not exist). | `ado done` exits 2 with `decisions-xcheck-marker`. | After the merge, `link-on-merge` **refuses** to close that story and fails the job. The story stays open (if the log shows `decisions-xcheck-unreachable` instead of `-marker`, the token identity can bypass rules: fix the PAT, run it again). | `decisions-xcheck-marker` |

**A red run is recorded as a match only if two things hold:** the toolkit checkout and install steps of that job succeeded, **and** the last column's pattern
matches the log (for an "absent" pattern: `grep` finds nothing). A job that is red for any other reason (a missing secret, a runner error, a different rule)
is a mismatch, whatever its colour. The patterns are anchored to what `gh run view --log` *renders*, because that log also prints each step's own script, and
the scripts contain the plain texts: an annotation renders as `##[error]` or `##[warning]` where the script says `::error::` or `::warning::`, and a script's
`echo` line never ends where the rendered line does (`\.$`). The branch guard installs the toolkit and checks the branch in one step, so a red guard counts
only when the `##[error]Branch` annotation is present, not when the step is merely red.

**Hosted setup for (e) and (f).** The comment ids of the fixture (10, 11, 12) do not exist on a real story. On the smoke story, post the decisions with
`ado decisions <story> --post` (preview, then `--apply`, as the token identity, which makes it a kit author); have an approver (a human identity, never
the token identity, per the recommended setup) comment `/approve D1`; run `ado decisions <story> --sync`; commit the decisions file on the branch. For
(f) then edit the `posted:` marker's comment id to one that is not the kit's posting.

**What the local run does not cover.** `run_local.py` runs `gate` with `--offline` and without `--src` or `--changed`, `strict check` without `--head`, and
`done` with `--base main` where the hosted close step uses the pre-merge base SHA on the merged commit. A local/hosted mismatch is the finding this step
exists to find: stop, record it, and open a fix task.

**Expected:** the fourth column for all six, with the log text of the last column, plus the vendored strict-verify results for (c) and (d).

**Rollback:** close the pull requests and delete their branches, remove both secrets, take the access setting back, and (only when the whole consumer
repository is to go) delete it. Reopen any story that case (e) closed. The owner then revokes the GitHub PAT in GitHub settings and the Azure DevOps
PAT in Azure DevOps, and removes the `delete_repo` scope again.

```bash
gh pr close <number> -R praveenlavu/ci-smoke-consumer --delete-branch --comment "ci smoke case withdrawn"
gh secret delete ADO_TOKEN -R praveenlavu/ci-smoke-consumer
gh secret delete TOOLKIT_READ_TOKEN -R praveenlavu/ci-smoke-consumer
gh api -X PUT repos/praveenlavu/spec-kit-ado/actions/permissions/access -f access_level=<the value recorded in Prepare>       # Owner: restores the earlier setting
ado update <story> --state "<the state it had before>"       # a story that case (e) closed
gh auth refresh -s delete_repo && gh repo delete praveenlavu/ci-smoke-consumer --yes       # Owner: deleting needs the delete_repo scope
gh auth refresh --remove-scopes delete_repo       # Owner: take the scope off the token again, right after the delete
```

## Step 4: the same cases with `pipelines/*.yml` in the smoke project

**Who:** Controller for the runs; Owner for the project's connection to the toolkit repository and the PAT variable.

Use the Azure Repos repository of the smoke project (`spec-kit-ado-smoke`) with the templates from [ci-azure-pipelines.md](ci-azure-pipelines.md),
`ref: <RC>` in `resources.repositories`, and a build validation policy on `main` so a pull request starts the pipeline. The `az` commands need the
`azure-devops` extension (`az extension add --name azure-devops`); run `az repos pr create --help` and `az pipelines runs list --help` first and
adjust a flag that your version names differently, because these were not run. The owner authenticates `az` himself, in his own shell, with `az devops login --organization https://dev.azure.com/<org>` (it prompts for the PAT) or
`AZURE_DEVOPS_EXT_PAT` set there; the PAT is never passed in argv or written to a file.

```bash
az repos pr create --organization https://dev.azure.com/<org> --project spec-kit-ado-smoke --repository <repo> \
  --source-branch <branch> --target-branch main --title "<title> AB#<story>" --work-items <story>
az pipelines runs list --organization https://dev.azure.com/<org> --project spec-kit-ado-smoke --branch refs/pull/<pr>/merge --top 1
az pipelines runs show --organization https://dev.azure.com/<org> --project spec-kit-ado-smoke --id <run>
```

**Expected:** the same outcomes as the right-hand column of step 3, case by case: (a) the branch guard failed and the gate green, (b) failed, (c) green,
(d) failed, (e) the story closed and no cross-check finding, (f) the close refused with `decisions-xcheck-marker`. On Azure the `ADO_TOKEN` is the build service identity (`$(System.AccessToken)`): it must not hold "Bypass rules on work item
updates" (a project administrator's build identity may; check it) and is not an approver, and `decisions-xcheck-unreachable` in (f) instead of `-marker` means it can bypass. The pipeline log shows rule ids
only, never test output or ledger text. Check the repository's merge-type policy too: the one-parent guard assumes "Merge (no fast-forward)" or
"Squash" only, which has not been verified on a real project.

**Rollback:** abandon the pull requests, remove the build validation policy and the pipeline definitions made for the run, and reopen any story
case (e) closed.

```bash
az repos pr update --organization https://dev.azure.com/<org> --id <pr> --status abandoned
az pipelines delete --organization https://dev.azure.com/<org> --project spec-kit-ado-smoke --id <pipeline> --yes
ado update <story> --state "<the state it had before>"
```

## Step 5: acceptance criterion 8, the unattended run

**Who:** Owner (his machine, his approver identity, and the second human identity that posts the approving comments); Controller watches the log.

Run it again only if anything it depends on changed since it was last proven (the poller, the approval grammar, the workflow). Follow
[unattended.md](unattended.md): `approval: "comment"` and the approvers on the target branch, then a 5-minute poller with no terminal.

```bash
ado approvals sync <run-id> --dry-run
ado approvals sync <run-id> --resume
ado status <story>
```

**Expected:** from "pull" to an open pull request with no TTY; the only human acts are the approvers' comments; the poller's log shows each paused
gate resumed. `ado status <story>` ends on the PR stage.

**Rollback:** stop the poller (remove the cron line, or `launchctl unload ~/Library/LaunchAgents/<poller>.plist`) and leave the run paused. Nothing in
ADO is undone by that; remove a test PR and its work items by hand if they should not stay.

## Step 6: tag, release, move `v0` last

**Who:** each sub-step below names exactly one, Owner or Controller. The Owner does 6.3, 6.4, 6.6, 6.7 and 6.8; the Controller does 6.1, 6.2 and 6.5.
6.4 is the point of no return: a public repository and a public tag are cached by others. Everything before 6.4 can be undone completely.
6.1 to 6.3, 6.6 and 6.8 run **in one clone on one machine**: `RC`, the local tag `v0.3.0` and the directory `tmp` that 6.2 creates live there, so the owner runs
his steps in that clone (a clone or a machine of his own would not have them, and the commands would fail).

**Expected:** the tag is the verified `RC`, the bundle builds from it and its checksum verifies, the repository reads `PUBLIC`, the shipped strict-verify
template gives the same results as the vendored copy did, every release URL answers 200 with the checksum that `SHA256SUMS` holds, and `v0` points at
`v0.3.0`; each sub-step states its own check.

**Rollback:** each sub-step names its own; the whole is the [Global rollback](#global-rollback). Before 6.3 the only undo needed is `git tag -d v0.3.0`.

Steps 6.1, 6.2, 6.3, 6.6 and 6.8 are the commands of the [go-public checklist](catalog-submission.md#go-public-checklist), repeated here so that this file
is complete. If the two ever differ, fix both in the same change (a test compares them).

**6.1 Tag the verified release candidate locally and check that the tag serves every file.** Who: Controller. Nothing is pushed until the checks pass. `RC` is
the SHA every step above proved, never the moving `main`:

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
)
```

The block is a subshell on purpose: its `exit 1` ends only the subshell, so pasting it into an interactive shell never closes that shell and loses `RC`. A
failure prints its message and the subshell's status is non-zero: do not go on to 6.3.

*Expected:* no refusal and no `MISSING` line, and the version line printed. *Rollback:* `git tag -d v0.3.0`.

**6.2 Build the bundle from the tag, not the working tree.** Who: Controller. The checksum file uses the bare file name, so `shasum -c` works for the people
who download it:

```bash
tmp=$(mktemp -d) && git worktree add "$tmp/src" v0.3.0
specify bundle validate --path "$tmp/src/bundle" --offline
specify bundle build --path "$tmp/src/bundle" --output "$tmp/out"
(cd "$tmp/out" && shasum -a 256 ado-0.3.0.zip | tee SHA256SUMS)
(cd "$tmp/out" && shasum -a 256 -c SHA256SUMS)
echo "$tmp"
```

*Expected:* validation passes, `ado-0.3.0.zip` exists, the check prints `ado-0.3.0.zip: OK`, and the directory printed last is handed to the owner for 6.6.
*Rollback:*

```bash
git worktree remove --force "$tmp/src"
rm -rf "$tmp/out"
```

**6.3 Push the tag.** Who: Owner, in the clone where 6.1 ran, on the same machine.

```bash
git push origin v0.3.0
```

*Expected:* the tag is on the remote. *Rollback:* before anything consumed it, `git push origin :refs/tags/v0.3.0` and `git tag -d v0.3.0`; after that see
[Global rollback](#global-rollback), "A bad tag or release".

**6.4 Make the repository public.** Who: Owner, only after steps 1 to 5 and 6.1 to 6.3 passed. Then he enables private vulnerability reporting, branch
protection on `main`, and secret scanning with push protection, in the repository settings:

```bash
gh repo edit praveenlavu/spec-kit-ado --visibility public --accept-visibility-change-consequences
gh repo view praveenlavu/spec-kit-ado --json visibility --jq .visibility
```

*Expected:* the second command prints `PUBLIC`. *Rollback:* [Global rollback](#global-rollback), "Making the repository private again".

*After the flip* (one docs commit): remove the text that says nothing is published: the README "Status of the release" paragraph, the
"Status: unreleased" paragraph of `bundle/README.md`, the "0.3.0 is not tagged yet" paragraph in `docs/quickstart.md` section 1 (it ships to
consumers) and the DRAFT banner of `docs/catalog-submission.md`, and drop the "while private" / `TOOLKIT_READ_TOKEN` wording from the CI docs.

**6.5 Re-run the (c) and (d) strict-verify legs through the shipped file.** Who: Controller, after 6.4. The consumer's `strict-verify` job now calls the real
reusable workflow with no vendored copy and no token: `uses: praveenlavu/spec-kit-ado/.github/workflows/ado-strict-verify.yml@<RC>` with
`toolkit-ref: <RC>`. Push that change to the (c) and (d) branches (a new commit starts the run), then:

```bash
gh pr checks <number> -R praveenlavu/ci-smoke-consumer --watch
gh run view <databaseId> -R praveenlavu/ci-smoke-consumer --log | grep -E 'spec-kit-ado: strict verify passed\.$'
gh run view <databaseId> -R praveenlavu/ci-smoke-consumer --log | grep -F 'green-fails'
```

*Expected:* (c) green, and its log has a line matching `spec-kit-ado: strict verify passed\.$` (anchored: the template's own script also contains the words, so an
unanchored match proves only that the step ran); (d) **fails**, its log shows `green-fails`, and it has **no** line matching the (c) pattern; the same as the
vendored copy did in step 3.
*Failure path:* if (c) is red, or (d) is green, or either is red for another reason, **STOP**. Do not run 6.6 to 6.8: **no GitHub release**, no filing,
and `v0` is not moved. The Owner deletes the tag, which nothing has consumed yet (`git push origin :refs/tags/v0.3.0`, a tag push and so owner-only; the
controller then removes the local one with `git tag -d v0.3.0`; see [Global rollback](#global-rollback)). The owner decides whether to make the repository private again (`gh repo edit praveenlavu/spec-kit-ado --visibility
private --accept-visibility-change-consequences`), and the fix goes forward as 0.3.1 with a new `RC` from step 1. *Rollback:* as step 3.

**6.6 Create the release and check every URL.** Who: Owner, in the clone where 6.1 ran, on the same machine. `tmp` is the directory 6.2 printed; `--verify-tag` aborts the release if the tag is not already on
the remote, so GitHub never creates one at the tip of the default branch:

```bash
tmp=<the directory printed by 6.2>
gh release create v0.3.0 "$tmp/out/ado-0.3.0.zip" "$tmp/out/SHA256SUMS" -R praveenlavu/spec-kit-ado --title "v0.3.0" --notes-file <file holding the CHANGELOG 0.3.0 section> --verify-tag
for u in \
  https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.3.0.zip \
  https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.3.0/ado-0.3.0.zip \
  https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/workflows/ado-lifecycle/workflow.yml \
  https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/extensions.json \
  https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v0.3.0/catalog/workflows.json; do
  printf '%s %s\n' "$(curl -sLo /dev/null -w '%{http_code}' "$u")" "$u"; done
curl -sL https://github.com/praveenlavu/spec-kit-ado/releases/download/v0.3.0/ado-0.3.0.zip | shasum -a 256
```

*Expected:* every line starts with `200`, and the last checksum equals the one in `SHA256SUMS`. *Rollback:* [Global rollback](#global-rollback),
"A bad tag or release".

**6.7 Clean-project tests and the filings.** Who: Owner. The clean-project installs (extension, workflow, bundle by artifact and by id) and the three
catalog filings are steps 7 to 10 of the [go-public checklist](catalog-submission.md#go-public-checklist); the filings are made as his account. Do not file
anything before 6.6 passes. *Rollback:* "Retracting a catalog filing" below.

**6.8 Move `v0` last,** after everything above passed, then test one `@v0` consumer once. Who: Owner. First record where `v0` points now: the rollback depends on
whether it exists (`git ls-remote --tags origin v0` prints a commit, or nothing). Run in the same clone as 6.1:

```bash
git ls-remote --tags origin v0
git tag -f v0 v0.3.0 && git push -f origin v0
```

*Expected:* a consumer pinned to `@v0` runs the same checks as with `@<RC>`. This is the only tag that ever moves ([VERSIONING.md](../VERSIONING.md)).
*Rollback:* if `v0` existed, move it back to the previous release tag: `git tag -f v0 <previous tag> && git push -f origin v0` (for example `v0.2.0`). If the
first command printed nothing, `v0` did not exist before, and the undo is to delete it: `git push origin :refs/tags/v0` and `git tag -d v0`.

## Global rollback

Use the smallest one that fits. Consumers who pin a SHA or an exact tag are not affected by any of these; tell the ones who pin `@v0` first.

**Disable and fix forward (the default).** A defect found after the release is fixed in a new version, `x.y.z+1`, and `v0` moves to it. A release
tag is never moved or reused: if `v0.3.0` itself is wrong, cut `v0.3.1` ([VERSIONING.md](../VERSIONING.md)).

**Moving `v0` back.** If `v0` existed before 6.8: `git tag -f v0 <previous tag> && git push -f origin v0`, then check with `git ls-remote origin refs/tags/v0`
that it points at the previous release. If it did not exist: `git push origin :refs/tags/v0` and `git tag -d v0`.

**A bad tag or release.** Only when nothing has consumed it (otherwise cut the next version instead). The release first, then the tag, local and
remote (Owner):

```bash
gh release delete v0.3.0 -R praveenlavu/spec-kit-ado --yes
git push origin :refs/tags/v0.3.0
git tag -d v0.3.0
git ls-remote --tags origin v0.3.0       # prints nothing once it is gone
```

**Making the repository private again** (Owner). This stops new readers; it cannot recall what was already cloned, forked or cached, so treat
anything that was public as seen:

```bash
gh repo edit praveenlavu/spec-kit-ado --visibility private --accept-visibility-change-consequences
gh repo view praveenlavu/spec-kit-ado --json visibility --jq .visibility       # prints PRIVATE
```

**Retracting a catalog filing** (Owner, as the account that filed it). Close the issue or pull request and say why; do not edit the entry in place:

```bash
gh pr close <number> -R github/spec-kit --delete-branch --comment "Withdrawn: a defect was found in the release; a corrected version will be filed."
gh issue close <number> -R github/spec-kit --reason "not planned" --comment "Withdrawn: a defect was found in the release; a corrected version will be filed."
```

A listing that was already merged is removed by a new pull request against the catalog file, not by this checklist.

## Record sheet

Task P4-21 fills this in. Until it has run, every row is `NOT RUN`. Record the date, the `RC` SHA, the Python and Spec Kit versions, the run URL,
the exit codes and rule ids seen, and anything that differed from the expected column.

| Step | Expected | Actual | Result |
|---|---|---|---|
| 1 `ci` on `main`, Python 3.9 and 3.13 | `test (3.9)`, `test (3.13)`, `scrub`, `actionlint` green; baseline tests ran | | NOT RUN |
| 2 contract job, minimum and latest | both legs green, none skipped | | NOT RUN |
| 3 consumer PRs (a) to (f) on GitHub, vendored strict-verify for (c) and (d) | per the table in step 3, log text present | | NOT RUN |
| 4 the same cases on Azure Pipelines | per step 4 | | NOT RUN |
| 5 unattended run (AC 8) | open PR with no terminal | | NOT RUN |
| 6.1 to 6.4 tag the `RC`, bundle, push the tag, make public | tag equals `RC`, `ado-0.3.0.zip: OK`, `PUBLIC` | | NOT RUN |
| 6.5 strict-verify (c) and (d) through the shipped file | (c) green, (d) fails with `green-fails` | | NOT RUN |
| 6.6 to 6.8 release, filings, `v0` last | every URL 200, checksum equal, `v0` at `v0.3.0` | | NOT RUN |
