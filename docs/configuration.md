# Configuration: `ado.config.json`

The `ado` CLI and the `speckit.ado.*` commands read `ado.config.json` from the current
directory, or from the path in `$ADO_CONFIG` when that is set. The file holds names and
settings only, never a token, and is meant to be committed. A full example with every
key at its default, except `story_type`, `done_state` and `removed_state`, is in
[ado-config.template.json](../ado-config.template.json). Those three are left out on purpose: they are the
keys `ado setup` detects from your project, and it fills in only keys that are absent, so a copy of the template
lets detection run on any process template (a key you copy in would be kept as is, even if wrong for your project).
Without them the defaults apply: `User Story`, `Done` and `Removed`, which fit Scrum's Done but not Agile's `Closed`.

`ado setup` writes `organization` and `project` and keeps every other key already in the
file. With the verified PAT it also looks up the project's requirement type
(`workitemtypecategories/Microsoft.RequirementCategory`) and that type's `Completed` and
`Removed` states, and writes `story_type`, `done_state` and `removed_state` for any of
the three not already set (Agile gives `User Story` / `Closed` / `Removed`; Scrum gives
`Product Backlog Item` / `Done` / `Removed`). If the story type and the Task type
disagree on the Completed state, it asks you to pick one. If a lookup fails it prints a
one-line note and leaves the defaults. It also appends `.ado/` to `.gitignore` beside the
config, unless the line is already there. If the file exists but is not a JSON object, `ado setup` refuses to overwrite it
and exits 2. `speckit.ado.setup` adds `stack` after you confirm the phase map.

## Keys

Every string in the file, values and object keys at any depth, must be free of control characters (C0, DEL, C1, U+2028/2029, surrogates, private use, and invisible format characters such as bidi overrides, BOM and soft hyphen; the joiner and direction marks U+200C-U+200F are allowed); otherwise `Config.load` fails with `ConfigInvalid` naming the key (exit 3 in CI), and `ado setup` refuses to write such a value, because the file comes from the PR itself and could otherwise inject lines into CI logs.

**In CI the config is untrusted.** The gate and the branch guard read `ado.config.json` (and the ledger) from the PR's own checkout, so the PR's author controls every key, including `gate.block` and `branch_prefix`. That is why an invalid file fails the gate closed (exit 3) rather than being skipped, why control characters are rejected, and why anything CI echoes from it is cut to one line with control characters replaced by spaces. Protect the file with `CODEOWNERS` and branch protection if `gate.block` matters to you (see the CI docs' trust model).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `organization` | string | required | ADO organization, as in `https://dev.azure.com/<organization>`. Letters, digits, `.`, `_`, `-`, starting with a letter, digit, `_` or `-`; anything else is a config error (it goes into a URL path). |
| `project` | string | required | ADO project name. Any non-empty string. |
| `api_version` | string | `"7.1"` | ADO REST API version sent with every call. Any string; not validated. |
| `dod_path` | string | `"dod"` | Directory for ledgers (`<dod_path>/DoD_<id>.md`), a path inside the repo, relative to where you run `ado` (the repo root). `ado gate` reads the ledger only if it is a regular file (at most 1 MiB, UTF-8), not a symlink, whose real path is under `--src` (a symlinked directory that stays inside `--src` is read); otherwise it reports the ledger `unreadable` and skips every ledger check. The other commands read it as before. |
| `gate` | object | `{"tests": {}, "block": []}` | Gate settings, below. A partial object keeps the defaults for missing keys. |
| `attachments_allow` | list of strings | `[".md", ".svg", ".txt"]` | File extensions, each with its leading dot and compared case-insensitively, that `ado attach` and `ado done` may upload (anything else aborts) and `ado attachments --download` may save (anything else is listed as skipped). |
| `review_block_on` | list of strings | `[]` | Review severities that stop `speckit.ado.pr` (for example `["blocker"]`). Non-empty: `ado pr create` itself refuses (exit 2, no plan, nothing sent) when the ledger committed on `--head` logs no review, the latest review says `Blocks PR: yes` or counts a finding of a listed severity, or it is stale (its SHA is not an ancestor of `--head`, or later commits change files outside `<dod_path>/` and the spec dir the ledger recorded at the reviewed commit; that spec dir counts when it holds a regular-file `spec.md` or `tasks.md` at the reviewed commit, or, if it did not exist then (the spec is first committed after the review, in `speckit.ado.pr` step 2a), at `--head`. Spec edits after the review are not compared with what was reviewed (the review records no spec hash), and every file under that directory counts, whatever its kind; this guards against agent drift, not against an author who controls the config); it also refuses when `origin/<head>` differs from the local branch without `--push`, when the ledger has an unclosed code fence or `<!-- -->` comment (a ledger from before `ado dod` escaped story text: refresh it) or one hiding a `## 2.` heading, and when `git` or the head branch is unavailable; git replace refs and grafts are ignored. Empty means review is advisory. Each entry must be `blocker`, `major` or `minor` (case-sensitive); any other value is a config error (exit 2). |
| `story_type` | string | `"User Story"` | Requirement work item type of the project (`ado setup` detects it). |
| `done_state` | string | `"Done"` | A state name of the project (not validated). State `ado done` moves the story to, and the state ticked child Tasks move to on `ado push`. |
| `removed_state` | string | `"Removed"` | The project's Removed-category state (`ado setup` detects it). |
| `active_state` | string | `"Active"` | State `speckit.ado.pull` offers to move the story to. Read by that command from the file, not validated by the CLI. |
| `task_type` | string | `"Task"` | Work item type `ado push` creates under the story. A work item type of the project (not validated). |
| `repo_host` | `"github"` or `"azure"` | `"github"` | Where PRs are opened: `gh` for GitHub, the ADO REST API for Azure Repos. Only `"azure"` selects Azure Repos; any other value behaves as GitHub. `ado pr comment` and `ado branch <id> --link` work only with `azure`. |
| `repository` | string | `""` | Azure Repos repository name. Required when `repo_host` is `azure`. |
| `branch_prefix` | string | `"feature/"` | Start of the feature branch name: `ado branch <id>` names it `<branch_prefix>ab<id>-<slug>` (e.g. `feature/ab1001-count-active-members`), and `<branch_prefix>ab<n>` at the start of a branch name ties it to story `<n>` (as does `AB#<n>` anywhere). A plain `<branch_prefix><n>-…` does not: Spec Kit's own `001-…` / `20261028-143022-…` branches never read as a story, whatever the prefix. May be empty; otherwise letters, digits, `.`, `_`, `-`, `/` starting with a letter or digit, with no `..`, `//`, `/.` or `.lock/`. |
| `mode` | `"advisory"` or `"strict"` | `"advisory"` | `"advisory"` keeps every v0.2 behaviour. `"strict"` turns on strict SDD+TDD: the effective `gate.block` and `review_block_on` are your lists plus the strict floor (`gate.block` gains `ac_traceability`, `assertion`, `open_questions`, `commits_without_story`; `review_block_on` gains `blocker`), with your entries kept first and no duplicates. In strict mode `speckit.ado.review` also runs its AC fidelity plane (see the README, Strict mode, L3). Any other value is a config error (exit 2; `ado gate` exits 3). |
| `strict` | object | `{"test_command": null, "test_timeout": 300}` | Strict-mode settings; a partial object keeps the defaults. `strict.test_command`: a string of 1-500 characters, the command the strict test run executes. `strict.test_timeout`: an integer from 1 to 3600, the seconds that run may take. An unknown key (reported by position, never echoed), a value of the wrong type or range, or a `strict` that is not an object is a config error. |
| `stack` | object | `{"kit": "", "phases": {}}` | Phase map for routing; see [stack-kits.md](stack-kits.md). |

In strict mode `ado pr create`, `ado done` and `ado gate` use the effective lists (`gate.block` plus the floor, `review_block_on` plus `blocker`), and `pr create` and `done` run the `pr` and `done` strict check first (exit 2, nothing sent). With `mode: "strict"`, `ado strict check <id> --phase implement|pr|done` (optional `--base REF`, `--head REF`) applies the strict rules (see the README's Strict mode section); the `implement` phase refuses while `strict.test_command` is unset, and the `pr` and `done` phases check the red/green commit evidence in ledger section 3 (L1; test files are the `gate.tests` globs plus the built-in ones; a red commit may also change only the story's `DoD_<id>.md` and `Decisions_<id>.md` in `dod_path` and the spec dir's `tasks.md`). L1 reads git history, so CI must use a full clone (`fetch-depth: 0`). `ado strict override <id> <rule> --reason "<text>"` (a human, in a terminal; not a security boundary) records an override that lets one of `test-command`, `l2`, `open-question` or `placeholder` pass for the story. `ado strict verify <id>` runs `strict.test_command` at each AC's red commit (must exit non-zero) and green commit (must exit 0) and at `HEAD` (must exit 0), each run limited to `strict.test_timeout` seconds, and records the result in ledger section 4; `ado strict check` then validates that record (`record-missing`, `record-stale`, `record-incomplete`, `record-failed`, `record-command-mismatch`) without running anything. **`strict.test_command` is code from your project**: the test runner executes it at one commit in a temporary checkout (a throwaway `git clone --shared --no-checkout` outside the repo, no shared config, hooks or index), with a scrubbed environment for the command and for every git call (an allow-list: no `GIT_*`, `ADO_TOKEN`, `GH_TOKEN`, `*_TOKEN`, `*_PAT`, `*_SECRET`, `*_KEY`, `*_PASSWORD`, `AWS_*`, an empty temporary `HOME`), no stdin, a timeout of `strict.test_timeout` seconds and no output returned. It is hygiene, not a sandbox: the command keeps your user's filesystem and network access, can read and change its own checkout's `.git`, and a process that calls `setsid` escapes the timeout kill. Global and system git config are ignored, so Git LFS filters do not run and LFS files are checked out as pointer files. CI must run it without secrets: check out with `persist-credentials: false` (the shipped workflows do) and run the test command in a job where no step holds secrets. `l2` also covers `test-command`, so one `l2` override lets an unset `strict.test_command` pass. `ado strict verify` honours overrides like `check`: with an `l2` or `test-command` override in force it runs no tests, records nothing and exits 0 with `verification skipped: overridden` (and `overrides_used`) (with `--no-record`, as in CI, an override in force is instead the finding `override-in-force` and exit 2 unless `--allow-override` is passed).

### CI trust: the config comes from the target branch

In CI the PR controls the files it is judged by, so `ado gate --base REF`, `ado strict check --base REF`, `ado strict verify --base REF` and `ado branch --id-from NAME --base REF` (both CI templates pass `--base origin/<target branch>` to the gate and the story-id step, and ask `ado strict mode --base origin/<target branch>` first) read `ado.config.json` from the **target branch** first, with `git show` at the full ref (`refs/remotes/REF`, then `refs/heads/REF`, then a full 40-hex commit id; never a tag, a short name or an option-like value). If that file exists and says `mode: "strict"`, the **whole config is the target's**: `gate.block`, `gate.tests`, `dod_path`, `branch_prefix`, `review_block_on`, `strict.test_command`, `strict.test_timeout` and the rest; the gate's commit range is also walked from the resolved commit id, so a tag named like the base cannot move it. The PR's own `ado.config.json` is not read, so a PR cannot lower `gate.block`, set `mode` to `advisory`, point `strict.test_command` at `true` or make every file a test with `gate.tests: {"**": ...}`. The `gate` summary or PR comment then carries one extra line, `config: target branch (strict)` (JSON key `config`); when `--base` is given and the PR's own config is strict, it says `config: pull request`; with an advisory config nothing is added.

- The target has **no** `ado.config.json`, or says `advisory` (or no `mode`): the PR's config applies, exactly as in v0.2, and a PR that adds strictness is judged by it. The first PR that introduces strict mode is therefore judged by its own config (the target has none yet); from its merge on, the target's config applies.
- The target's file is unreadable, invalid (the same checks as above, nesting too deep included), larger than 1 MiB, not a regular file, or git fails: `ado gate` and `ado branch --id-from` exit **3**, so CI fails closed (`strict check` and `strict verify` exit 2, an `error:` line and no JSON). The message never echoes the ref or the file's text.
- REF is no commit in the checkout (a shallow clone: use `fetch-depth: 0`; or `origin/main` in a repo with no origin): the target cannot be read. If the PR's own config is **strict** (or invalid) that fails closed as above (exit 3 for `gate`, 2 for the strict verbs). If it is advisory or has no `mode`, the v0.2 path applies unchanged: the gate notes that commits were not checked and does not block on it.
- Without `--base` nothing changes: the working tree's config is used.
- **Only the repo-root `ado.config.json` is trusted.** With a non-default `config-path` (GitHub) or `ADO_CONFIG`, the target's root file is what is read; if the target has none, the PR's config applies silently.
- **Protect what judges the PR** on the target branch with branch protection rules and `CODEOWNERS` (required review from code owners): `ado.config.json`, and the CI definition itself, which is PR-controlled too (the GitHub caller workflow and its `toolkit-ref`; the Azure PR pipeline YAML). The ledger and decisions file are still the PR's own files; the strict checks read them but judge them with the target's rules.
- **What the shipped templates now enforce, and what is still yours.** Under a strict target they fail (not skip) when the PR has no story id, run `ado strict check <id> --phase pr --head <head sha> --base origin/<target>` after the gate (no token; rule ids only in the comment) and fail on it, and the link-on-merge templates fail when the strict `done` check refuses. Protecting the workflow or pipeline files, the caller workflow and `toolkit-ref`, and `ado.config.json` stays your job (branch rules or `CODEOWNERS`); the Actions and Azure agents have not been run in this project's environment, so the template logic is covered by `bash -n` and real-bash tests with a fake `ado` only.

`organization` and `project` must be present and non-empty, `story_type` and
`removed_state` and `done_state` must be non-empty strings when set, `gate`, `stack` and `strict` must be
objects, `mode` must be `advisory` or `strict`, `attachments_allow` must be a list of strings, `branch_prefix` must be
git-ref-safe as above, `gate.block` entries must be known check names (`ac_traceability`,
`assertion`, `ac_drift`, `open_questions`, `commits_without_story`, `open_decisions`) and `review_block_on` entries
must be `blocker`, `major` or `minor`; otherwise the CLI exits 2
with a message naming the problem.

An invalid config file (present but failing any check above) is explicit misconfiguration, not
missing information, so `ado gate` and `ado branch --id-from` exit **3** instead of 2 (every other
verb keeps 2). The CI gates fail on an invalid config: GitHub posts `ADO gate FAILED:
ado.config.json is invalid: <message>` as the PR comment and turns the check red; Azure Pipelines fails
the build with an error annotation carrying the first line of the CLI message and posts no PR thread
(`ado pr comment` needs the same config); the workflow's `gate-check` halts on exit 3. A missing config
file or token stays exit 2 and advisory.

## `gate`

```json
"gate": {
  "tests": { "**/*.spec.rb": "\\bexpect\\(" },
  "block": ["ac_traceability"]
}
```

- `tests` maps a test-file glob to a regex that counts as an assertion. Your entries are
  added to the built-in set (Python, JS/TS, Java/Kotlin, C#, Go, Apex), and an entry with
  the same glob replaces the built-in one. Files matching any glob are where the gate
  looks for the test IDs named in ledger section 3.
  The gate reads only regular files of at most 1 MiB that really live under `--src`: it never follows a symlink (not even one pointing inside the repo), and it skips FIFOs, devices and larger files. Because the PR supplies both the files and these regexes, reading through a symlink would let a PR probe files outside the repo one bit at a time. The ledger `ado gate` reads must meet the same rules (else it is reported unreadable). Skipped files are counted in the summary (`N file(s) skipped: ...`), never named.
  Each glob must be 1 to 200 characters with every `[...]` class closed and valid, and each regex 1 to 500 characters and compilable; otherwise it is a config error (exit 3 for `ado gate`) that names the entry by position (`entry #2`), never by its text, because the config comes from the PR and the message reaches the CI log.
- `block` lists the checks that may fail the gate (exit 1, and a failed CI check). Valid names are exactly the six below; any other entry is a config error (exit 2; exit 3 and a red CI check for `ado gate`), so a typo such as `open_question` cannot silently switch a check off:
  - `ac_traceability` — an AC row in section 3 has no test (an empty or placeholder
    tests cell) or names only tests that cannot be found under `--src`. The AC are counted
    from the `### Acceptance Criteria` lines in ledger section 1 (AC-1 is the first line): every AC line in
    section 1 without a row in section 3 is unmapped, so a ledger whose section 3 is empty or still holds the
    placeholder row blocks when `ac_traceability` is listed here. Only a ledger with no AC lines
    and no AC rows is reported as not evaluated, and that never blocks.
  - `assertion` — a changed test file has no assertion match.
  - `ac_drift` — the story's acceptance criteria changed since the ledger was seeded.
  - `open_questions` — ledger section 2 still has an open question (`- [ ] Q<n>: <question> → _(open)_ · owner: <role>`; open means an unticked box or an `_(open)_` answer). Always reported as `open_questions` in the gate output; it fails the gate only when listed here.

  - `commits_without_story` — a non-merge commit in `<base>..HEAD` (`ado gate --base REF`, which the CI templates set to `origin/<target branch>`; without it, the branch `origin/HEAD` points at) whose message lacks this story's `AB#<id>` (word-bounded: `xAB#2` does not count, nor does another story's `AB#3`). Always reported as `commits_without_story` (short SHAs); when the base cannot be resolved (`--base` ref not found or starting with `-`, no `origin/HEAD`) or git fails, `commits_note` says the commits were not checked and the gate does not block. Fails the gate only when listed here.

  - `open_decisions` — the story's decisions file `<dod_path>/Decisions_<id>.md` (not the ledger) still has a `D<n>` line that is open or `rejected`; a `validated` one is resolved. Reported as `open_decisions` (ids in numeric order) **only when that file has at least one `D<n>` line**, so a repo without a decisions file keeps the v0.2 output exactly. A `D<n>` line that breaks the grammar, an unclosed fence or comment in the file, or a file that exists but cannot be read (a symlink, outside `--src`, over 1 MiB, not UTF-8) adds `?` to the list, which blocks when listed. Fails the gate only when listed here.

  Missing evidence (a `green` row without a commit or PR) is always a warning only.

  Drift is reported as **not evaluated** (`drift.evaluated: false`, with a `note`) whenever it cannot be compared: `--offline`, no token or ADO unreachable, or a ledger with no AC hash. That is never the same as "no drift", and it never blocks, even with `ac_drift` in `block`.

With `block` empty the gate is advisory: it reports and exits 0. Missing information
never fails it: no ledger, an unreadable ledger, or ADO being unreachable or the token missing (drift is then
not evaluated, with a note that says why) each produce a note and exit 0. Test files that match no glob are not
scanned for assertions; add a `tests` entry for your language to cover them.

## Credentials

The token is not part of this file. The CLI looks for it in this order:

1. `$ADO_TOKEN` (CI sets this; in Azure Pipelines from `$(System.AccessToken)`).
2. The store `ado setup` wrote to: the macOS Keychain, or `~/.azdo/<org>.token` (mode
   0600) on Linux and as the macOS fallback. On Windows it is the same plaintext file
   under your user profile; mode 0600 has no effect there, so only the profile's access
   control protects it.

A personal token needs **Work Items: Read & Write**, and **Project and Team: Read** for `ado setup` and `ado branch --link`, which read the project record (Microsoft documents that call as needing it; not tested with a token limited to exactly these scopes). With `repo_host: azure`, opening or
commenting on PRs through `ado pr` also needs **Code: Read & Write** (a token allowed to write to Azure Repos), and
`ado branch <id> --link` (the branch in the story's Development section) needs **Code: Read**
(it looks up the repository id) and **Project and Team: Read**; a 401/403 from either means that scope is missing. On GitHub, PRs go
through `gh`, so no Code scope is needed.

## Exit codes

`0` success; `1` an ADO/HTTP error, a failed item in a batch, a blocking gate, or a DoD
checklist that is not ready (`ado branch --id-from` also exits 1 when the branch names no story); `2` a config, credential or
argument problem, or a plan that changed since approval (`--expect-plan`); `3` only for `ado gate`,
`ado branch --id-from` and `ado strict mode`: `ado.config.json` is present but invalid (CI fails on it).
