"""`ado status <id>`: the first unmet stage of a story and the one command that moves it on. Read-only: local files, git and a few ADO reads
(the story with its relations, its comments, the child Tasks); nothing is written anywhere.

`STAGES` is data: `(id, needs_board, met(ctx), next(ctx))`, in lifecycle order. A board stage needs the network: offline, with no client, or after
one failed read it is NOT CHECKED (listed in `not_checked`), never met. Every text here is fixed: stage ids, counts and the story id (a validated
int) only; no ledger, spec, decision, comment or ADO text and no error message is ever printed. A file that cannot be read (a symlink, outside the
working tree, over 1 MiB, not UTF-8) leaves its stage unmet. Process help, not a security boundary: the ledger, the spec dir and the decisions
file come from the branch and can be edited.

Run awareness (P4-5): the newest Spec Kit run state of this story (`.specify/workflows/runs/<run id>/state.json`, a file the agent can edit) says whether
a run is paused, failed, aborted or stuck, and the exact resume command. That file is UNTRUSTED: read confined (no symlink, directory under the runs
dir only, 1 MiB cap, run id by regex, no path built from file text), only known statuses and step ids of the shipped workflow are accepted, `output`,
`error`, `execution` and every other field are never used, and every printed command is a fixed template (story id: validated int; run id: regex)."""
import datetime
import json
import os
import re
import types

from . import comments, decisions, dod, gate as gate_mod, prs, push, questions, rules, stack, strict, tasks, workitems
from .auth import AuthError
from .client import AdoError
from .config import Config, ConfigError, ConfigInvalid

RUN_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*")  # Spec Kit's own pattern (engine.py _RUN_ID_PATTERN)
RUN_STATUSES = ("created", "running", "paused", "completed", "failed", "aborted")
RUN_WORKFLOW = "ado-lifecycle"
RUNS_DIR = (".specify", "workflows", "runs")
MAX_RUN_DIRS = 2000
STUCK_AFTER = datetime.timedelta(hours=1)
RUN_UNREADABLE = "Run: {n} run file(s) of this story are not readable by this ado version; use specify workflow status"
RUN_IGNORED = "Run: {n} run file(s) ignored (oversized, a symlink or not valid JSON); use specify workflow status"
RUN_TRUNCATED = "Run: older runs not examined (more than 2000 run directories); use specify workflow status"
_LOOP_N = re.compile(r"[0-9]{1,6}")  # ASCII digits only (R:33: nested loop steps are `<loop>:<step>:<n>`)
SHIPPED_STEPS = (
    "pull", "stack-check", "review-spec", "questions-loop", "questions-preview", "approve-questions", "questions-apply", "questions-applied", "plan",
    "plan-questions-loop", "plan-questions-preview", "approve-plan-questions", "plan-questions-apply", "plan-questions-applied", "wait-po-answers",
    "plan-with-answers", "answers-questions-loop", "answers-questions-preview", "approve-answers-questions", "answers-questions-apply",
    "answers-questions-applied", "review-plan", "tasks", "push-loop", "push-preview", "approve-push", "push-apply", "push-applied",
    "strict-check-implement", "implement", "decisions-loop", "decisions-preview", "approve-decisions", "decisions-apply", "decisions-applied",
    "strict-verify", "sync-loop", "sync-preview", "approve-sync", "sync-apply", "sync-applied", "gate", "gate-check", "refresh-context", "review",
    "review-review", "pr-clear", "pr-prepare", "pr-loop", "pr-preview", "approve-pr", "pr-apply", "pr-applied", "wait-po-validation",
)
SHIPPED_GATES = (
    "review-spec", "approve-questions", "approve-plan-questions", "wait-po-answers", "approve-answers-questions", "review-plan", "approve-push",
    "approve-decisions", "approve-sync", "review-review", "approve-pr", "wait-po-validation",
)
# Constants, not read from the installed workflow.yml (the agent can edit that file): tests/test_status.py pins both against workflows/ado-lifecycle/workflow.yml.
BOARD_NOT_CHECKED = "board-not-checked"  # the answer when every stage that could be read is met but a board stage could not be
STRICT_ONLY = ("stack-profile", "verified", "decisions-closed")
_BOARD_ERRORS = (AdoError, AuthError, ValueError, KeyError, TypeError, AttributeError)  # ADO unreachable, or an answer that is no work item
_GUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
# Recorded on the smoke projects: an Azure Repos PR is `vstfs:///Git/PullRequestId/<project guid>%2f<repo guid>%2f<number>`, a GitHub PR (Azure Boards
# GitHub app) is `vstfs:///GitHub/PullRequest/<repo guid>%2f<number>`; `vstfs:///GitHub/Commit/...` and `vstfs:///Git/Ref/...` are other links.
_PR_URL = re.compile(rf"vstfs:///Git/PullRequestId/{_GUID}%2f{_GUID}%2f[1-9][0-9]{{0,9}}|vstfs:///GitHub/PullRequest/{_GUID}%2f[1-9][0-9]{{0,9}}", re.I)
_GATE_HEADING = re.compile(r"^### Gate\b[^\r\n]*", re.M)
_BLOCKING = re.compile(r"^- blocking:\s*(yes|no)\s*$", re.M | re.I)


class _Down(Exception):
    """A board read failed (or the board is not read at all): the board stages are not checked."""


class Ctx:
    def __init__(self, client, story_id, repo, offline):
        self.id, self.repo, self.client, self.reason, self._memo = story_id, repo, client, "", {}
        self.down = "offline" if offline or client is None else None  # why the board is not read: offline | error
        self.config_state = "ok"
        try:
            self.cfg = Config.load(repo)
        except ConfigInvalid:
            self.cfg, self.config_state = None, "invalid"
        except ConfigError:
            self.cfg, self.config_state = None, "missing"
        self.strict_mode = self.cfg is not None and self.cfg.mode == "strict"

    @property
    def s(self):
        """The strict check's own context: confined reads of the ledger, the spec dir files and the decisions file."""
        if "s" not in self._memo:
            self._memo["s"] = strict._Ctx(types.SimpleNamespace(config=self.cfg), self.id, self.repo)
        return self._memo["s"]

    def memo(self, key, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def board(self, fn):
        """fn() for a board read; any failure ends all board reads for this run (the stage is then not checked)."""
        if self.down:
            raise _Down
        try:
            return fn()
        except _BOARD_ERRORS:
            self.down = "error"
            raise _Down from None

    def story(self):
        return self.memo("story", lambda: self.board(lambda: workitems.get(self.client, self.id)))

    def tasks(self):
        text = self.s.file("tasks.md")
        return None if text is None else tasks.parse_tasks(text)

    def ops(self):
        """The dry-run plan of `ado push` (GET and WIQL only), once."""
        return self.memo("ops", lambda: self.board(lambda: push.plan_push(self.client, self.id, self.tasks())))


def _n(count, what):
    return f"{count} {what}"


# ---- met(ctx): True, or False with ctx.reason set to a fixed text (else the stage's default in _WHY) ----
def _config(c):
    c.reason = "ado.config.json is invalid" if c.config_state == "invalid" else "no ado.config.json in this directory"
    return c.cfg is not None


def _ledger(c):
    if c.s.ledger is None:
        return False
    if dod.unclosed(c.s.ledger):
        c.reason = ("the ledger has an unclosed code fence or comment in section 1, so the sections after it cannot be read" if dod._repair_at(c.s.ledger) != -1
                    else "the ledger has an unclosed code fence or comment outside section 1: close it by hand (a refresh refuses it)")
        return False
    return True


def _ledger_next(c):
    if c.s.ledger is None:
        return f"speckit.ado.pull {c.id}"
    return f"ado dod {c.id} --refresh" if dod._repair_at(c.s.ledger) != -1 else f"ado status {c.id}"  # by hand, then look again


def _stack_profile(c):
    result, _, findings = stack.show(c.repo, "strict", c.cfg.strict["test_command"])
    c.memo("stack_status", lambda: result["status"])
    return findings == 0


def _stack_profile_next(c):
    return "ado stack approve" if c.memo("stack_status", lambda: "none") in ("draft", "changed-since-approval") else "speckit.ado.stack"


def _rules(c):
    led = c.s.ledger
    frm, rev = dod.ledger_rules_from(led), dod.ledger_rules_reviewed(led)
    files = rules.local_files(c.cfg, led, c.repo, base=c.repo)
    cov = rules.coverage(led, {}, files, frm, rev, c.s.decisions, "pr")
    c.memo("rules_cov", lambda: cov)
    if cov["header"] or cov["missing"] or cov["format"]:
        c.reason = ("the ledger has no valid `Rules from:` line" if "rules-from" in cov["header"] else
                    _n(len(cov["missing"]) + len(cov["format"]), "rules file(s) named by `Rules from:` are missing, unusable or in another format"))
    elif "rules-reviewed" in cov["header"]:
        c.reason = "`Rules reviewed:` is malformed: a human repairs the ledger header by hand, then confirms"
    elif cov["changed"]:
        c.reason = _n(len(cov["changed"]), "rule(s) differ from the hash recorded in `Rules reviewed:`: a human re-confirms them in their own terminal")
    else:
        c.reason = _n(len(cov["unconfirmed"]), "in-scope rule(s) are not confirmed yet: run the confirm yourself, no terminal is needed")
    return not (cov["header"] or cov["missing"] or cov["format"] or cov["unconfirmed"] or cov["changed"])


def _rules_next(c):
    cov = c._memo["rules_cov"]
    if "rules-from" in cov["header"]:
        return f"ado dod {c.id} --refresh"
    if cov["missing"] or cov["format"]:
        return f"ado rules {c.id}"
    return f"ado rules {c.id} --confirm"


def _questions_posted(c):
    plan = c.board(lambda: questions.plan_post(c.client, c.id, questions.parse_questions(c.s.ledger)))
    return plan["action"] in ("none", "unchanged")


def _questions_answered(c):
    opened = questions.parse_questions(c.s.ledger)["open"]
    if any(q["id"] == "?" for q in opened):
        c.reason = "ledger section 2 has an unclosed code fence or comment, so its questions cannot be read"
    else:
        c.reason = _n(len(opened), "open question(s) in ledger section 2 wait for an answer")
    return not opened


def _plan(c):
    if c.s.spec_dir is None:
        c.reason = "the ledger records no Spec dir, so plan.md cannot be found"
    return c.s.file("plan.md") is not None


def _tasks(c):
    return bool(c.tasks())


def _board_tasks(c):
    ids = [t["id"] for t in c.tasks()]
    if len(set(ids)) != len(ids):
        c.reason = "tasks.md repeats a task id, so the board cannot be compared"
        return False
    n = sum(op["action"] == "create" for op in c.ops())
    c.reason = _n(n, "task(s) in tasks.md have no Task on the board")
    return n == 0


def _tasks_ticked(c):
    n = sum(not t["done"] for t in c.tasks())
    c.reason = _n(n, "unticked task(s) in tasks.md")
    return n == 0


def _board_sync(c):
    n = sum(op["action"] == "update" for op in c.ops())
    c.reason = _n(n, "task(s) differ between tasks.md and the board")
    return n == 0


def _decisions_posted(c):
    s = c.s
    if not s.decisions_ok or s.decisions["unparsed"]:
        c.reason = "the decisions file cannot be read in full (unreadable, a line breaks the grammar, or a fence or comment is open)"
        return False
    todo = decisions.unposted(s.decisions)
    if todo:
        c.reason = _n(len(todo), "decision line(s) not posted to the story")
        return False
    lines = s.decisions["open"] + [d for d in s.decisions["validated"] if d["kind"] == "override"]
    wanted = {d["posted"]["comment_id"] for d in lines if d["posted"]}
    if not wanted:
        return True
    # a `posted:` marker is a local claim: it counts only when a decisions comment with that id is on the story (not proof of who wrote it)
    found = c.board(lambda: {x["id"] for x in comments.fetch(c.client, c.id, with_id=True) if x["text"].startswith(decisions.MARKER)})
    c.reason = _n(len(wanted - found), "posted marker(s) name a decisions comment that is not on the story")
    return wanted <= found


def _verified(c):
    found = strict._record_done(c.s) if _landed(c) else strict._record(c.s)
    rule = found[0]["rule"] if found else ""
    c.reason = {"record-missing": "ledger section 4 has no verification record",
                "record-stale": "the verification record is stale: the code changed after it"}.get(
                    rule, "the verification record is incomplete, failing or made with another test command")
    return not found


def _headings(ledger_md, kind):
    """The `### <kind>` heading lines of ledger section 4 (outside fences and comments), a trailing ` (superseded)` removed."""
    log = dod._visible(dod.section(ledger_md, 4) or "")[0]
    return [re.sub(r" \(superseded\)$", "", m.group(0).rstrip()) for m in re.finditer(rf"^### {kind}\b[^\r\n]*", log, re.M)]


def _landed(c):
    """The work is on the default branch: the ledger as committed there (`git show`, read through `strict._blob_at`) holds the latest review (else gate)
    heading of this ledger. A commit that merely names the story does not count. After a squash or rebase merge the recorded SHAs are no ancestors of
    HEAD, so the staleness of the gate, review and verification record is not judged then (as `ado done` does)."""
    def go():
        base = strict.default_base(c.repo)
        commit, rel = (strict._resolve_base(c.repo, base) if base else ""), strict._ledger_rel(c.cfg.dod_path, c.id)
        mine = (_headings(c.s.ledger, "Review") or _headings(c.s.ledger, "Gate"))[-1:]
        if not (commit and rel and mine):
            return False
        try:
            raw = strict._blob_at(c.repo, commit, rel, gate_mod.MAX_READ, "the story ledger at the default branch")
            there = _headings(raw.decode("utf-8"), "Review") + _headings(raw.decode("utf-8"), "Gate") if raw is not None else []
        except (ConfigError, ValueError):  # ConfigInvalid is a ConfigError; UnicodeDecodeError is a ValueError
            return False
        return mine[0] in there
    return c.memo("landed", go)


def _logged_sha(c, heading):
    sha = prs._HEADING_SHA.search(heading.group(0))
    return sha.group(1) if sha and prs._SHA_TEXT.fullmatch(sha.group(1)) else None


def _gate(c):
    log = dod._visible(dod.section(c.s.ledger, 4) or "")[0]
    live = list(_GATE_HEADING.finditer(log))
    if not live:
        c.reason = "ledger section 4 has no gate result"
        return False
    rest = log[live[-1].end():]
    nxt = re.search(r"^#{1,3} ", rest, re.M)
    verdicts = _BLOCKING.findall(rest[:nxt.start()] if nxt else rest)
    sha = _logged_sha(c, live[-1])
    if sha is None or len(verdicts) != 1:
        c.reason = "the latest gate result has no commit or no single `blocking:` line"
        return False
    if verdicts[0].lower() == "yes":
        c.reason = "the latest gate result is blocking"
        return False
    if not _landed(c) and strict._record_stale(c.repo, "HEAD", sha, c.cfg.dod_path, c.s.spec_dir, c.id):
        c.reason = "the gate result is stale: the code changed after it"
        return False
    return True


def _review(c):
    why = prs.review_blocks(c.id, c.cfg.effective_review_block_on() or ["blocker"], "HEAD", c.cfg.dod_path, c.repo, push=True, squash=_landed(c))
    if why is None:
        return True
    c.reason = ("no review is logged in ledger section 4 of HEAD" if why.startswith("no review logged")
                else "the latest review blocks the PR" if why.startswith("latest review blocks")
                else "the review is stale: the code changed after it" if why.startswith("review is stale")
                else "the review could not be checked (a ledger or git problem)")
    return False


def _pr_link(c):
    rels = c.story().get("relations") or []
    return any(isinstance(r, dict) and r.get("rel") == "ArtifactLink" and isinstance(r.get("url"), str) and _PR_URL.fullmatch(r["url"]) for r in rels)


def _merged(c):
    """The work has landed on the default branch `origin/HEAD` points at (see `_landed`). Local only: fetch after the merge."""
    if not strict.default_base(c.repo):
        c.reason = "origin/HEAD is not set, so the default branch cannot be compared"
        return False
    c.reason = "the default branch does not hold this story's latest review or gate result yet (merge the PR, then fetch)"
    return _landed(c)


def _done_state(c):
    fields = c.story().get("fields") or {}
    return fields.get("System.State") == c.cfg.done_state


def _done_state_next(c):
    # `ado done` refuses in strict mode while a decision is open: the command that closes them comes first
    return f"ado decisions {c.id}" if c.strict_mode and _open_decisions(c) else f"speckit.ado.done {c.id}"


def _open_decisions(c):
    s = c.s
    if not s.decisions_ok or s.decisions["unparsed"]:
        return -1
    eff = decisions.effective(s.decisions)
    return len(eff["open"]) + len(eff["rejected"])


def _decisions_closed(c):
    n = _open_decisions(c)
    c.reason = ("the decisions file cannot be read in full" if n < 0 else _n(n, "decision(s) are open or rejected"))
    return n == 0


_WHY = {
    "config": "no valid ado.config.json", "ledger": "no readable ledger for this story (missing, a symlink, outside the working tree, over 1 MiB or not UTF-8)",
    "stack-profile": "the stack profile is not approved, drifts from strict.test_command, or leaves a detected root uncovered",
    "rules": "the business rules are not pulled, or not confirmed in the ledger",
    "questions-posted": "the open questions are not (or no longer) posted to the story",
    "questions-answered": "questions wait for an answer", "plan": "no plan.md in the spec dir", "tasks": "no tasks.md with tasks in the spec dir",
    "board-tasks": "tasks are missing on the board", "tasks-ticked": "tasks are unticked", "board-sync": "board states differ from tasks.md",
    "decisions-posted": "decisions are not posted to the story", "verified": "no current verification record",
    "gate": "no current, non-blocking gate result in the ledger", "review": "no current, non-blocking review in the ledger",
    "pr-link": "the story has no pull request link", "merged": "the pull request is not merged",
    "done-state": "the story is not in the done state", "decisions-closed": "decisions are open or rejected",
}
STAGES = [
    ("config", False, _config, lambda c: "ado doctor" if c.config_state == "invalid" else "ado setup"),
    ("ledger", False, _ledger, _ledger_next),
    ("stack-profile", False, _stack_profile, _stack_profile_next),
    ("rules", False, _rules, _rules_next),
    ("questions-posted", True, _questions_posted, lambda c: f"ado questions {c.id} --post --plan-out .ado/plan-questions.md"),
    ("questions-answered", False, _questions_answered, lambda c: f"speckit.ado.plan {c.id}"),
    ("plan", False, _plan, lambda c: f"speckit.ado.plan {c.id}"),
    ("tasks", False, _tasks, lambda c: f"speckit.ado.tasks {c.id}"),
    ("board-tasks", True, _board_tasks, lambda c: f"ado push {c.id} --plan-out .ado/plan-push.md"),
    ("tasks-ticked", False, _tasks_ticked, lambda c: f"speckit.ado.implement {c.id}"),
    ("board-sync", True, _board_sync, lambda c: f"ado push {c.id} --plan-out .ado/plan-sync.md"),
    ("decisions-posted", True, _decisions_posted, lambda c: f"ado decisions {c.id} --post --if-any --plan-out .ado/plan-decisions.md"),
    ("verified", False, _verified, lambda c: f"ado strict verify {c.id}"),
    ("gate", False, _gate, lambda c: f"speckit.ado.gate {c.id}"),
    ("review", False, _review, lambda c: f"speckit.ado.review {c.id}"),
    ("pr-link", True, _pr_link, lambda c: f"speckit.ado.pr {c.id}"),
    ("merged", False, _merged, lambda c: "git fetch origin" if strict.default_base(c.repo) else "git remote set-head origin --auto"),
    ("done-state", True, _done_state, _done_state_next),
    ("decisions-closed", False, _decisions_closed, lambda c: f"ado decisions {c.id}"),
    ("done", False, lambda c: True, lambda c: "none"),
]


# ---- run awareness: Spec Kit's run state, read as untrusted data ----
def _step_ok(step, ids=SHIPPED_STEPS):
    if not isinstance(step, str):
        return False
    parts = step.split(":")
    return step in ids if len(parts) == 1 else len(parts) == 3 and parts[0] in ids and parts[1] in ids and _LOOP_N.fullmatch(parts[2]) is not None


def _when(text):
    """A timezone-aware ISO time as UTC, else None."""
    if not isinstance(text, str) or len(text) > 40:
        return None
    try:
        when = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.astimezone(datetime.timezone.utc) if when.tzinfo is not None else None


def _scan(repo, story_id):
    """`(newest run of this story or None, unreadable, ignored, truncated)`. A run file that is oversized, a symlink, unparsable or not tied to this
    story is ignored (the symlinked/oversized/unparsable ones are counted into `ignored`); one that is tied to it (workflow id and story id) but has an
    unknown status, a missing key or an unknown step id counts as `unreadable`. `truncated`: more than MAX_RUN_DIRS run directories, the rest unexamined."""
    base = os.path.realpath(repo)
    runs = os.path.join(base, *RUNS_DIR)
    if os.path.realpath(runs) != runs:  # .specify, workflows or runs is a link: not followed
        return None, 0, 0, False
    names, unreadable, ignored, best = [], 0, 0, None
    try:
        with os.scandir(runs) as it:
            for e in it:
                if RUN_ID.fullmatch(e.name):
                    if e.is_symlink():
                        ignored += 1
                    elif e.is_dir(follow_symlinks=False):
                        names.append(e.name)
    except OSError:
        return None, 0, 0, False
    truncated = len(names) > MAX_RUN_DIRS
    for name in sorted(names)[:MAX_RUN_DIRS]:
        path = os.path.join(runs, name, "state.json")
        if not os.path.lexists(path):
            continue
        text = gate_mod._read(path, base, set())
        try:
            d = json.loads(text) if text is not None else None
        except (ValueError, RecursionError):
            d = None
        if not isinstance(d, dict):
            ignored += 1
            continue
        inp = d.get("inputs")
        sid = inp.get("story_id") if isinstance(inp, dict) else None
        if not (d.get("workflow_id") == RUN_WORKFLOW and type(sid) is int and sid == story_id):
            continue
        when, state, step = _when(d.get("updated_at")), d.get("status"), d.get("current_step_id", False)
        if (d.get("run_id") != name or not isinstance(state, str) or state not in RUN_STATUSES or when is None
                or not (_step_ok(step) or (step is None and state == "created"))):
            unreadable += 1
            continue
        if best is None or (when, name) > best[:2]:
            best = (when, name, {"run_id": name, "status": state, "step": step or "", "updated_at": when.isoformat()})
    return (best[2] if best else None), unreadable, ignored, truncated


def read_run(repo, story_id):
    """`{"run_id", "status", "step", "updated_at"}` of the newest `ado-lifecycle` run of this story, else None (see `_scan`). The values are
    validated: the run id by Spec Kit's pattern, the status from a fixed list, the step among the shipped workflow's ids (`SHIPPED_STEPS`) (a nested loop step is
    `<loop>:<step>:<n>`), `updated_at` re-formatted from a parsed time. Nothing else of the file is read or kept."""
    return _scan(repo, story_id)[0]


def _stuck(c, run):
    """True/False: the run was last updated over 1 h before the SERVER's time; None when there is no server time (never the local clock)."""
    fn = None if c.down else getattr(c.client, "server_now", None)
    try:
        now = fn() if fn else None
    except Exception:  # noqa: BLE001 - any failure to get the server time means the age is not judged
        return None
    if not isinstance(now, datetime.datetime) or now.tzinfo is None:
        return None
    return now - _when(run["updated_at"]) > STUCK_AFTER


def _run_notes(c, run, counts, stage):
    notes = []
    if run:
        rid, st, step = run["run_id"], run["status"], run["step"]
        at = f" at {step}" if step else ""
        resume = f"specify workflow resume {rid}"
        if st == "paused":
            gate = step.split(":")[-2 if ":" in step else 0] in SHIPPED_GATES  # a nested step is `<loop>:<step>:<n>`
            notes.append(f"Run {rid}: paused at gate {step}. Answer it in a terminal: {resume}" if gate else f"Run {rid}: paused{at}. Resume: {resume}")
        elif st == "failed":
            notes.append(f"Run {rid}: failed{at}. Resume (it re-runs that step and everything after it): {resume}")
        elif st == "running":
            over = _stuck(c, run)
            notes.append(f"Run {rid}: running{at}; its age was not checked (no server time)." if over is None else
                         f"Run {rid}: running{at}, last update over 1 h ago. The run may have been killed: resume refuses a running run. If it was, set "
                         f'"status" to "failed" in .specify/workflows/runs/{rid}/state.json by hand, then: {resume}' if over else f"Run {rid}: running{at}.")
        elif st == "aborted":
            notes.append(f"Run {rid}: aborted{at}; resume refuses it. Bring the story up to date with: ado status {c.id} --catch-up")
        elif st == "completed":
            order = [sid for sid, *_ in STAGES]
            early = stage in order and order.index(stage) < order.index("pr-link")
            notes.append(f"Run {rid}: completed, but the story is at {stage}, before the PR: ado status {c.id} --catch-up" if early else f"Run {rid}: completed.")
        else:
            notes.append(f"Run {rid}: created, not started.")
    unreadable, ignored, truncated = counts
    notes += [RUN_UNREADABLE.format(n=unreadable)] * bool(unreadable) + [RUN_IGNORED.format(n=ignored)] * bool(ignored) + [RUN_TRUNCATED] * truncated
    return notes


def applicable(cfg):
    """The stage ids that apply to a config: the strict-only stages in strict mode, `rules` when `rules.enabled`."""
    return [sid for sid, *_ in STAGES if (sid not in STRICT_ONLY or cfg.mode == "strict") and (sid != "rules" or cfg.rules["enabled"])]


_STAGE = {row[0]: row for row in STAGES}


def _judge(c, sid):
    """`(ok, reason)` of one stage, once per Ctx: ok is True (met), False (unmet) or None (a board stage that could not be read)."""
    judged = c.memo("judged", dict)
    if sid not in judged:
        _, needs_board, met, _ = _STAGE[sid]
        c.reason = ""
        try:
            if needs_board and c.down:
                raise _Down
            judged[sid] = (bool(met(c)), c.reason)
        except _Down:
            judged[sid] = (None, "")
        except Exception as e:  # noqa: BLE001 - a stage that cannot be judged is unmet, with the exception CLASS only
            judged[sid] = (False, f"this stage could not be checked ({type(e).__name__})")
    return judged[sid]


def _follow(c, sid):
    try:
        return _STAGE[sid][3](c)
    except Exception:  # noqa: BLE001
        return "ado doctor"


def _walk(c):
    """(stage id or None, its next command, why, skipped board stage ids): the first unmet stage in order; a board stage that cannot be read is skipped."""
    keep = set(applicable(c.cfg)) if c.cfg else set(sid for sid, *_ in STAGES)
    skipped = []
    for sid, *_ in STAGES:
        if sid not in keep:
            continue
        ok, why = _judge(c, sid)
        if ok is None:
            skipped.append(sid)
        elif not ok:
            return sid, _follow(c, sid), why or _WHY[sid], skipped
    return None, "", "", skipped


# Catch-up, in the order the workflow runs the steps (implement, decisions, verify, sync, gate, review, PR). A board write is a preview, then the human's
# yes, then the apply with --expect-plan; a stage not in the table lists its own next command.
CATCH_ORDER = ("ledger", "stack-profile", "rules", "questions-posted", "questions-answered", "plan", "tasks", "board-tasks", "tasks-ticked",
               "decisions-posted", "verified", "board-sync", "gate", "review", "pr-link")
CATCH_UP = {
    "questions-posted": ("ado questions {id} --post --plan-out .ado/plan-questions.md", "ado questions {id} --post --apply --expect-plan .ado/plan-questions.md"),
    "questions-answered": ("speckit.ado.plan {id}",), "plan": ("speckit.ado.plan {id}",), "tasks": ("speckit.ado.tasks {id}",),
    "board-tasks": ("ado push {id} --plan-out .ado/plan-push.md", "ado push {id} --apply --expect-plan .ado/plan-push.md"),
    "tasks-ticked": ("speckit.ado.implement {id}",),
    "decisions-posted": ("ado decisions {id} --post --if-any --plan-out .ado/plan-decisions.md",
                         "ado decisions {id} --post --if-any --apply --expect-plan .ado/plan-decisions.md"),
    "verified": ("ado strict verify {id}",),
    "board-sync": ("ado push {id} --plan-out .ado/plan-sync.md", "ado push {id} --apply --expect-plan .ado/plan-sync.md"),
    "gate": ("speckit.ado.gate {id}",), "review": ("speckit.ado.review {id}",), "pr-link": ("speckit.ado.pr {id}",),
}
CATCH_UP_AGAIN = "Run `ado status {id}` again after each step: the plan-questions round and the `wait-po-answers` gate are not modelled here."
CATCH_UP_UNREAD = "Catch-up: local stages are all met; board stages not checked: run `ado status {id}` online."


def catch_up(c):
    """The ordered commands that bring the story to an open PR, each a fixed template with the story id: only stages that are NOT met. A local stage
    that is met lists nothing (its file is the evidence; a generation command would overwrite it); a board stage that is unmet, or could not be read,
    lists only its preview and apply (a `pr-link` that could not be read lists nothing: the PR may exist). Board stages that could not be read are
    listed only when something else is known to be unmet; with every local stage met the list is empty (the renderer says the board was not checked).
    Strict-only stages appear in strict mode only; a story past the PR link gets an empty list; without a config the setup command alone."""
    sid, follow, _, _ = c.memo("walk", lambda: _walk(c))
    if c.cfg is None:
        return [follow] if sid else []
    todo, unread = [], []  # (stage, commands) known unmet, and board stages that could not be read
    for stage in CATCH_ORDER:
        if stage not in applicable(c.cfg):
            continue
        ok, _ = _judge(c, stage)
        if ok is None:
            if stage != "pr-link":
                unread.append(stage)
        elif not ok:
            todo.append(stage)
    if "questions-posted" in todo and "questions-answered" in todo:
        todo.remove("questions-answered")  # the product owner answers only after the post: `ado status` says so again after the apply
    out = []
    for stage in CATCH_ORDER:
        if stage in todo or (todo and stage in unread):
            for cmd in CATCH_UP.get(stage) or (_follow(c, stage),):
                cmd = cmd.format(id=c.id) if stage in CATCH_UP else cmd
                if not out or out[-1] != cmd:
                    out.append(cmd)
    return out


def evaluate(client, story_id, repo=".", offline=False, with_catch_up=False):
    """`{"stage", "next", "why", "board_checked", "run", "run_notes", "not_checked", "board"}` (plus `"catch_up"`, a list of commands, when asked).
    `stage` is the first stage that is unmet; a board stage that could not be read is skipped and listed in `not_checked`; when nothing readable is
    unmet but a board stage was skipped the stage is `board-not-checked`, never `done`. `board` is why the board was not read (`offline` or `error`)
    or None. `run` is `read_run`'s answer (or None) and `run_notes` the fixed lines about it (see `_run_notes`)."""
    if type(story_id) is not int or not 1 <= story_id <= prs.MAX_ID:
        raise ValueError("the story id must be a work item id")
    c = Ctx(client, story_id, repo, offline)
    sid, follow, why, skipped = c.memo("walk", lambda: _walk(c))
    if sid is None and skipped:
        sid, follow = BOARD_NOT_CHECKED, f"ado status {story_id}" if c.down == "offline" else "ado doctor"
        why = "the board was not read (offline), so its stages are not checked" if c.down == "offline" else "ADO could not be read, so the board stages are not checked"
    elif sid is None:
        sid, follow, why = "done", "none", "every stage is met"
    try:
        run, *counts = _scan(repo, story_id)
    except Exception:  # noqa: BLE001 - run awareness never breaks the stage answer
        run, counts = None, [0, 0, False]
    todo = catch_up(c) if with_catch_up else None  # judged before `board` is read: it may be the read that fails
    res = {"stage": sid, "next": follow, "why": why, "board_checked": c.down is None and not skipped, "run": run,
           "run_notes": _run_notes(c, run, counts, sid), "not_checked": skipped, "board": c.down}
    if with_catch_up:
        res["catch_up"] = todo
    return res


def render(res, story_id):
    stage = "board not checked" if res["stage"] == BOARD_NOT_CHECKED else res["stage"]
    lines = [f"Story {story_id}: {stage}. Next: {res['next']}", f"Why: {res['why']}"]
    if res["not_checked"] and res["stage"] != BOARD_NOT_CHECKED:
        lines.append(f"Board not checked ({'offline' if res['board'] == 'offline' else 'ADO could not be read'}): {', '.join(res['not_checked'])}")
    lines += res["run_notes"]
    if "catch_up" in res:
        if res["catch_up"]:
            lines += ["Catch-up (in order; after each preview read it and decide before the apply):", *(f"{n}. {cmd}" for n, cmd in enumerate(res["catch_up"], 1)),
                      CATCH_UP_AGAIN.format(id=story_id)]
        else:
            lines.append(CATCH_UP_UNREAD.format(id=story_id) if res["board"] else "Catch-up: nothing to catch up.")
    return "\n".join(lines)
