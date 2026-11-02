# Unattended runs: the poller

This page is for running the `ado-lifecycle` workflow with nobody at a terminal. The approvers answer in the story's comment thread, and a small
command, `ado approvals sync`, reads those comments and resumes the paused run. It is process enforcement, not a security boundary: read the
residuals at the end.

## Spec Kit version

The verdict inputs of the `ado-lifecycle` workflow rely on the gate's `verdict_input`, which Spec Kit has had since 0.15.1. **Comment mode (the poller and `ado status`) needs Spec Kit 1.2.1 or later**: 0.15.1 honours `verdict_input`, but its `state.json` has no `inputs` or `execution`, so `ado status` finds no run and `ado approvals sync` refuses every run (a fixed refusal, nothing is resumed), and a resume inside a loop re-runs the loop head. 0.15.1 is the only version below 1.2.1 that was run; none between is claimed (what was actually run: [compatibility.md](compatibility.md)). **A
Spec Kit older than 0.15.1 ignores `verdict_input`**: `ado approvals sync --resume` resumes the run, the gate pauses again at the same step, and this repeats on every
poll with no error and no progress. Nothing is approved wrongly (the gate stays paused), but nothing moves either. If a run is paused at the same gate after several polls with
an approval in place, check `specify --version`.

## Where it can run

**On a local or persistent machine only (v0.3.0). Never on an ephemeral CI runner.** A Spec Kit run keeps its state in
`.specify/workflows/runs/<run id>/state.json` in the working tree. The run also needs that working tree, `.ado/` (the plan files the gates show), the AI
CLI's login and the PAT. None of these survives a throwaway runner, so a poller there would find nothing to resume (or resume a run that cannot continue).
Use your own machine, a server, or a VM that stays up. The CI templates of this kit (`ci-github.md`, `ci-azure-pipelines.md`) do not run the poller.

## Setup (the recommended one)

1. On the **target branch** (the default branch, `origin/HEAD`; never the working tree), set `"approval": "comment"` and `approvers` (see
   [configuration.md, Approvers and comment mode](configuration.md#approvers-and-comment-mode-the-recommended-setup)). `ado approvals sync` reads
   nothing else about trust, and in terminal mode it exits 2 with a fixed text and makes no request.
2. **The agent never runs as an admin or approver.** It runs as a Contributor **without** the "Bypass rules on work item updates" permission, with that
   identity's PAT. The approvers are separate human identities (no approver PAT anywhere in the agent's or the poller's environment). The poller's
   identity and the CI service identity are never approvers.
3. Start the run as usual (`specify workflow run ado-lifecycle --input story_id=<id>`). It pauses at the first gate and exits.
4. Find the run id: `ado status <id>` prints `Run <run id>: paused at gate ...`. Put that id in the schedule below.
5. Optional: set `unattended.approval_requests` to `true` on the target branch so the poller posts what each approver is to read (below).

## What `ado approvals sync RUN_ID [--resume] [--dry-run]` does

Run from the project root (the working tree of the run), as the same user and token as the run. In order, and nothing after a refusal:

1. The run id must be a Spec Kit run id (ASCII letters, digits, `-`, `_`, at most 128 characters). Then `state.json` of that run is read: it is an
   **untrusted** file (the agent can edit it), so it is read confined (no symlink, 1 MiB, in the runs directory), only the workflow id, run id, status,
   step and story id are used, and each is checked. The story id is the run's own `story_id` input.
2. A run that is not `paused` does nothing: it prints `{"gate": null, "verdict": "waiting", "paused": false}`, exit 0, makes no request and never resumes.
3. The paused step must be one of the 12 gates (`approvals.GATES`; a nested step `<loop>:<gate>:<n>` is its middle part). Anything else is exit 2
   before any command line is built or any request is made.
4. **Verdict.** `{"gate": ..., "verdict": "approve" | "reject" | "waiting"}` is printed as indented, multi-line JSON (with `bad_newer`, a count, when approver lines are pending;
   `request`; `note`, a fixed text; `resumed` with `--resume`).
   - A review gate (`review-spec`, `review-plan`, `review-review`) is judged by the hash of `approvals.artifacts` on **this** checkout: the same function and
     the same bytes as `ado approvals check`, so line endings (CRLF or LF) and HEAD matter. An approver's newest valid `/approve <gate> <hash>` is `approve`,
     a `/reject <gate> <hash>` is `reject`.
   - An apply gate (`approve-questions`, `approve-plan-questions`, `approve-answers-questions`, `approve-push`, `approve-decisions`, `approve-sync`,
     `approve-pr`) is judged by the hash of the **plan text** in its fixed plan file under `.ado/` (the file the gate shows, without the `⚠` first line
     that a plan over 190 lines gets), which is what the `--apply` hashes. The verb's gates are one target, as for the apply.
   - A wait gate (`wait-po-answers`, `wait-po-validation`) is met by the approvers' `/answer`, `/approve` and `/reject` comments on ADO only (for `wait-po-validation` the working tree's decisions file can only keep a decision open when its `posted:` line is not its newest posting or whose text differs from it, which is a consistency check, not a security control, and an unreadable one refuses with `E_DECISIONS_FILE`)
     (`approvals.wait_state`): met is `approve`, anything else `waiting`. It has no hash and no request.
   - The rules are those of the applies: newest valid command wins, an approval is stale when the artifact changed or an approval request is newer, and
     a newer malformed approver line means `waiting` (never `approve`). The bypass-rules refusal, no approvers and unreadable or cut-off comments exit 2.
5. **Approval request** (only with `unattended.approval_requests: true` and only while waiting; also while a malformed approver line is pending, because the
   request shows the exact line to copy): if the poller's identity has no unedited request for this gate and hash on the story, it reads the comments again
   and, if nothing has been approved or rejected in the meantime, posts one comment. See below. When an approval of this exact text exists but a request is
   newer than it, the output carries a fixed `note`: the approver must approve again.
6. **`--resume`** with a verdict: the run is read again (it must still be paused at the same step), then
   `specify workflow resume <run id> --input verdict_<gate>=<verdict> --input verdict_<other>= ...` runs (one `--input` for each of the 12 gates; the other 11
   are reset to empty, because a Spec Kit verdict input persists across resumes and a stale one would pass a later gate). It is an argument list with no shell,
   no stdin, a 6-hour time limit and this process's environment without git's repository selectors and pager settings. `specify` is looked up on a
   sanitized `PATH`: absolute entries only, none inside the project or the current directory (an empty or relative entry such as `.` would run a `specify`
   the project planted; the comparison is by directory identity, so a differently-cased spelling on a case-insensitive filesystem counts too); it is refused
   with a fixed text when it is not found, and so is a missing `ado` or `git` on that same `PATH` (the resumed steps run them), and the resumed run gets the same clean `PATH`, so keep `specify`, `ado` and
   `git` in directories outside the project. The run's output is thrown away; use `specify workflow status <run id>`. The gate names are underscore forms
   (`verdict_review_spec`).

Exit codes: 0, 1 (the resume failed or timed out, `specify` was not found, or an ADO request failed: reading the comments or posting the request), 2 (a refusal with a
fixed text). No other. The texts name gates, counts and ids only, never a
comment, a display name or anything from `state.json`.

`--dry-run` does everything except the request and the resume: it prints the verdict and `"request": "would-post"` where one would be posted.

### Approval-request comments

The approver may never see the repository, so with `unattended.approval_requests: true` (target branch; default `false`) the poller posts one comment per gate
and hash. Its first line is `[spec-kit-ado:approval-request] <gate> <sha256>`, followed by the line to copy (`/approve <gate> <hash>`, and the `/reject` one)
and the exact text the hash covers, `html.escape`d, with control and invisible characters shown as `<U+XXXX>` and **every line of that text prefixed with
`│ `**, so no line of a plan or spec can be copied as a command: the kit's `/approve` and `/reject` lines are the only lines of the comment that parse. The
approver comments the `/approve` line back as the first line of their own comment. A `/` right before `approve`, `reject` or `answer` in the shown text is written `<U+002F>`, so a long line that a browser wraps cannot start a copyable
command. **Over 500,000 characters** (the escaped text counts, and the message gives that count, so padding with
invisible characters can force this) the comment carries no text, no `/approve` or `/reject` line and no file pointer: it says that the text is only on the
runner's working tree (it is not committed), that a human must review it there, and that this gate **must not** be approved by comment (use terminal
mode for it, or make the plan smaller). That is an instruction, not a block: the marker line still holds the hash, and `approvals check` and the applies would
accept a hand-typed `/approve <gate> <hash>` for text the approver never saw.

This is **the one kit write without a preview and a gate** (owner ruling): the standing consent is the key on the target branch, which a pull request
cannot change. With the key off, no request is posted; the posting function itself refuses with a fixed text. It posts once per hash (the next poll
sees its own comment), at most 50 requests by the poller's identity on one story, and only the escaped artifact text that the approver is meant to read: nothing
else from a file or a comment goes into it. An approval that is older than a request for the same gate (or any gate of the same apply) is stale, so the
poller posts a request only while the gate is still waiting.

## Schedule it

One entry per paused run; remove it when the run completes. The command is cheap (a few reads of the story's comments) and prints one indented, multi-line JSON document.

cron, every 5 minutes (a plain user crontab; the PAT comes from the keychain or from the environment of the user, never written in the crontab):

```
PATH=/path/to/tools/bin:/usr/local/bin:/usr/bin:/bin
*/5 * * * * cd /path/to/project && /path/to/spec-kit-ado/.venv/bin/ado approvals sync 20261001-100000-ab12cd34 --resume >> .ado/poller.log 2>&1
```

launchd (macOS), `~/Library/LaunchAgents/com.contoso.ado-poller.plist`, loaded with `launchctl load`:

```
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.contoso.ado-poller</string>
  <key>ProgramArguments</key><array>
    <string>/path/to/spec-kit-ado/.venv/bin/ado</string><string>approvals</string><string>sync</string>
    <string>20261001-100000-ab12cd34</string><string>--resume</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/path/to/tools/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>WorkingDirectory</key><string>/path/to/project</string>
  <key>StartInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>/path/to/project/.ado/poller.log</string>
  <key>StandardErrorPath</key><string>/path/to/project/.ado/poller.log</string>
</dict></plist>
```

Both need the same environment as an interactive run: the resumed steps run `ado`, `git`, `specify` and the AI CLI, and cron and launchd start with a minimal
`PATH`, so both examples set one that lists the directories of those tools; the PAT (`ADO_TOKEN` or the keychain) and the AI CLI login must be available to the
scheduler too. Keep `specify`, the AI CLI and `ado` in directories outside the project directory, and put only such directories on `PATH`: the agent can write into the project,
and a `specify` or `git` it planted there would run with the poller's credentials. Use the **same token** the run uses (next section).

## The poller must be the posting identity

The wait gates accept the kit's questions and decisions comments only when the token identity that checks is the one that posted them, and every request
comment is matched to the poller's identity. So run the poller with **the same token identity that runs the workflow and posts the kit's comments**. If
that identity cannot be established (no token, an anonymous answer) `sync` refuses with a fixed text and resumes nothing. Rotating the token while
questions are open has the consequences described under "Gate checks" in [configuration.md](configuration.md#approvers-and-comment-mode-the-recommended-setup).
The poller passes its own identity to the excluded set (`approvals.excluded(..., ci_identity=)`), so it can never count as an approver even if it is listed as one.

## A run that was killed

A run that was killed (the machine slept, the terminal closed) stays `running` in its `state.json`, and `specify workflow resume` refuses a running run.
`ado status <id>` says so when the run's last update is over an hour old by the server's clock. The fix is by hand: set `"status"` to `"failed"` in
`.specify/workflows/runs/<run id>/state.json`, then `specify workflow resume <run id>` (it re-runs the step it stopped at and everything after it). The poller
never edits `state.json` and never resumes a run that is not `paused`.

## Residuals (stated, not closed)

- **The run's own workflow copy is not protected.** `specify workflow resume` executes the copy of the workflow kept with the run, `.specify/workflows/runs/<run id>/workflow.yml`,
  which the agent can edit. Like `state.json` it is an untrusted file: the poller does not read or check it, and does not protect against a tampered copy. What
  stays the authority is the check or apply behind each gate (`ado approvals check`, the comment inside the five applies), not what a resume runs.

- **Process enforcement, not a security boundary.** Anyone who can run `specify` can supply a verdict input; the authority is the check or the apply
  that follows the gate (`ado approvals check`, the comment inside the five applies), not the poller.
- **Poller identity = posting identity** (above). A different token is a refusal for the wait gates, not a silent pass.
- **Approval-request comments of any author make an approval stale; this blocks only.** The request marker is matched whoever wrote it, so a stranger
  can post one and force the approver to approve again. It can never approve anything.
- **An approval that lands between the poller's read of the comments and its post makes the approval stale** (the request is newer than it). The poller
  reads the comments again right before the post, which narrows that window and does not close it; the output then carries the fixed `note` to approve again.
- **A persisted verdict whose apply refuses ends FAILED: start a new run.** If a verdict was given for a plan that then changed (or the apply finds no
  matching comment), the apply exits 2 and the run fails; resuming it does not help. Start a new run once the approver has commented.
- The resumed run executes with the poller's environment and credentials, and its output is not logged; `state.json`, plan files and ledger text are
  untrusted input to the poller, which reads only the fields and files named above.
- **Overlapping polls can resume a run twice.** Spec Kit has no cross-process lock: two polls that both read `paused` before the first resume saves `running` both
  resume. Run one poller per run: a single launchd job (launchd never starts a second instance of a job that is still running), or `flock -n <lock file>` in
  front of the cron command (Linux; macOS has no `flock` by default, use launchd there). The poller re-reads the run right before the resume and requires the
  same step (a loop iteration `:n` to `:n+1` included), which narrows the window and does not close it.
- **Not tried live (0.3.0): the keychain read from cron or launchd.** Whether a scheduled job can read the token that `ado setup` stored in the macOS keychain is unverified; run
  the poller once under the scheduler (`--dry-run`) before relying on it.
- A paused step that is not one of the 12 gates, an unreadable `state.json` and a symlinked run directory are exit 2: the poller does not guess.
- The poller does not read `approvers` or `unattended` from the working tree. An unreadable target config is exit 2.
- **`ado doctor` does not catch an approver that can never approve (known 0.3.0 limitation).** Its `approvers` row compares approvers with the local token
  identity only, not with the CI identity or the authors of the kit's `[spec-kit-ado:*]` comments. Such an approver is already excluded from approving
  (`approvals.excluded`), so nothing unsafe happens; the cost is diagnosis: the gates read `waiting` for ever. To find it, run
  `ado comments <story id> --author-ids` and compare the ids with `approvers`. Doctor's `bypass` row does check the token identity's bypass-rules permission.
