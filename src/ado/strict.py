"""`ado strict check`: the rules of strict SDD+TDD mode (design D§4). Read-only: it never writes and never runs tests
(`run_at` below is the L2 test runner, called only by `verify`, which writes the verification record `check` validates).

Each phase is its own list of rule functions in PHASES; a later task appends to a list without touching `check`.
A rule takes a _Ctx and returns findings `{"rule", "detail", "fix"}`. The ledger and the spec dir come from the PR
in CI, so every file is read through gate._read (regular, <= 1 MiB, confined to the repo, never a symlink), and a
`detail` carries only ids, file names and counts: never ledger, spec or ADO text."""
import hashlib
import os
import posixpath
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

from . import decisions, dod, gate, prs, push, questions, tasks
from .auth import AuthError
from .client import AdoError

_PLACEHOLDER = re.compile(r"_\(\s*fill\s+in", re.I)
_BULLETS = (("Approach", "Approach"), ("Assumptions", "Assumptions"), ("Use cases & edge cases", "Use cases"))
_LABEL = re.compile(r"^\s{0,3}[-*+]\s+\*\*[^*]*\*\*")
_NEXT = re.compile(r"[-*+]\s|#")  # a column-0 bullet or heading ends the bullet being read


def _finding(rule, detail, fix):
    return {"rule": rule, "detail": detail, "fix": fix}


class _Ctx:
    def __init__(self, client, story_id, repo):
        self.client, self.id, self.repo, self.cfg = client, story_id, repo, client.config
        self.ledger = gate._read(Path(repo) / dod.dod_file(self.cfg.dod_path, story_id), repo, set())
        spec_dir = dod.ledger_spec_dir(self.ledger) if self.ledger is not None else None
        self.spec_dir = spec_dir.strip() or None if spec_dir else None  # a CRLF ledger leaves `\r` on the header value
        self._files = {}

    def file(self, name):
        """Text of <spec dir>/<name>, or None (no spec dir, missing, symlink, outside the repo, too big, not UTF-8)."""
        if self.spec_dir is None:
            return None
        if name not in self._files:
            self._files[name] = gate._read(Path(self.repo) / self.spec_dir / name, self.repo, set())
        return self._files[name]


def _ledger(c):
    if c.ledger is None:
        return [_finding("ledger-missing", "the ledger is missing or unreadable", f"ado dod {c.id}")]
    return []


_SPEC_FILES = (("spec.md", "spec-missing", "run speckit.specify (or the stack kit's specify command)"),
               ("plan.md", "plan-missing", "run speckit.ado.plan"),
               ("tasks.md", "tasks-missing", "run speckit.ado.tasks"))


def _spec_files(c):
    if c.ledger is None:
        return []
    out = []
    for name, rule, fix in _SPEC_FILES:
        if c.spec_dir is None:
            out.append(_finding(rule, f"the ledger records no Spec dir, so {name} cannot be found",
                                f"set the `Spec dir:` line in the ledger header, then {fix}"))
        elif c.file(name) is None:
            out.append(_finding(rule, f"{name} is missing or unreadable in the spec dir", fix))
    return out


def _bullet_problem(lines, label):
    """True when the section 2 bullet is absent, empty (nothing but its label) or still holds `_(fill in`."""
    i = next((i for i, line in enumerate(lines) if re.match(rf"\s{{0,3}}[-*+]\s+\*\*{re.escape(label)}", line)), None)
    if i is None:
        return True
    text = [_LABEL.sub("", lines[i])]
    for line in lines[i + 1:]:
        if _NEXT.match(line):
            break
        text.append(line)
    body = "\n".join(text)
    return not body.strip() or _PLACEHOLDER.search(body) is not None


def _placeholders(c):
    if c.ledger is None:
        return []
    lines = dod._visible(dod.section(c.ledger, 2) or "")[0].splitlines()  # fences and comments render as nothing
    bad = [name for name, label in _BULLETS if _bullet_problem(lines, label)]
    if not bad:
        return []
    return [_finding("placeholder", f"ledger section 2 is missing, empty or still a placeholder for: {', '.join(bad)}",
                     "fill them in with speckit.ado.plan; no `_(fill in` may be left in Approach, Assumptions or "
                     "Use cases & edge cases")]


def _open_questions(c):
    if c.ledger is None:
        return []
    n = len(questions.parse_questions(c.ledger)["open"])  # an `assumed:` answer is closed; an unclosed fence counts as open
    if not n:
        return []
    return [_finding("open-question", f"{n} open question(s) in ledger section 2",
                     f"get them answered (ado questions {c.id} --post), or record the answer as `assumed: ...` and tick the line")]


_BOARD_FIX = "check access with `ado setup`, then re-run: strict mode needs the board to be readable"


def _board(c):
    text = c.file("tasks.md")
    if text is None:
        return []  # already reported as tasks-missing
    parsed = tasks.parse_tasks(text)
    if not parsed:
        return [_finding("tasks-missing", "tasks.md lists no tasks", "run speckit.ado.tasks")]
    ids = [t["id"] for t in parsed]
    if len(set(ids)) != len(ids):
        return [_finding("board-unchecked", "tasks.md has duplicate task ids, so the board was not checked",
                         "make every task id in tasks.md unique, then re-run")]
    try:
        ops = push.plan_push(c.client, c.id, parsed)  # read-only: GET/WIQL only
    except (AdoError, AuthError, ValueError, KeyError, TypeError, OSError) as e:
        return [_finding("board-unchecked", f"the board could not be read ({e.__class__.__name__})", _BOARD_FIX)]
    n = sum(op["action"] == "create" for op in ops)
    if not n:
        return []
    return [_finding("board-task-missing", f"{n} task(s) in tasks.md have no Task on the board", f"ado push {c.id} --apply")]


def _test_command(c):
    if c.cfg.strict.get("test_command"):
        return []
    return [_finding("test-command", "strict.test_command is not set", "set strict.test_command in ado.config.json")]


# ---- L1 order evidence (D§5): red <sha> → green <sha> per AC, checked against git ----
_HEX = r"[0-9a-fA-F]{7,40}"
_EVIDENCE = re.compile(rf"(?<![0-9A-Za-z])red[\s:]+({_HEX})(?![0-9A-Za-z])\s*(?:→|->)\s*"
                       rf"green[\s:]+({_HEX})(?![0-9A-Za-z])", re.I)
_TAGGED = re.compile(r"(?<![0-9A-Za-z])(?:red|green)[\s:]+[0-9a-f]{4,}", re.I)  # a second pair makes the cell ambiguous


def parse_evidence(status_cell):
    """`red <sha> → green <sha>` (or `->`; backticks, bold and trailing words tolerated) -> {"red","green"}, else None.
    The cell is PR text: a SHA is 7-40 hex characters or the cell is not evidence, so nothing else reaches git."""
    text = re.sub(r"[`*_]", "", status_cell or "")
    m = _EVIDENCE.search(text)
    return {"red": m[1], "green": m[2]} if m and len(_TAGGED.findall(text)) == 2 else None


# replace refs and old-style grafts rewrite history as git reports it: never let them hide a change
_GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat", "GIT_OPTIONAL_LOCKS": "0"}


def _git(repo, *args, env=None, timeout=30):
    """A finished git run (bytes), or None when git is missing, cannot start or runs past `timeout` s (30): a failure, never a pass."""
    try:
        return subprocess.run(["git", "--no-pager", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
                              check=False, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                              env={**os.environ, **_GIT_ENV} if env is None else env)
    except (OSError, subprocess.SubprocessError):
        return None


def _out(repo, *args, **kw):
    """stdout (bytes) of a successful git run, else None."""
    r = _git(repo, *args, **kw)
    return r.stdout if r is not None and r.returncode == 0 else None


def _commit(repo, sha, **kw):
    """Full id of the one commit whose id starts with the hex `sha`, else None. Object lookup only: a branch or tag
    named like the SHA never stands in, and an ambiguous prefix or a non-commit object is refused."""
    out = _out(repo, "rev-parse", f"--disambiguate={sha.lower()}", **kw)
    objs = out.decode("ascii", "replace").split() if out is not None else []
    return objs[0] if len(objs) == 1 and _out(repo, "cat-file", "-t", objs[0], **kw) == b"commit\n" else None


def _is_merge(repo, full):
    """True for a merge commit, and when its parents cannot be read (fail closed)."""
    out = _out(repo, "rev-list", "--no-walk", "--parents", full)
    return out is None or len(out.split()) > 2


def _is_ancestor(repo, older, newer):
    r = _git(repo, "merge-base", "--is-ancestor", older, newer)
    return r is not None and r.returncode == 0  # 1 = no, anything else = error: both count as no


def _changed(repo, full, diff_filter=None):
    """Paths the commit changes, NUL-delimited (names are untrusted), or None when git failed."""
    out = _out(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", "--no-renames",
               *([f"--diff-filter={diff_filter}"] if diff_filter else []), full, "--")
    return [n for n in out.decode("utf-8", "replace").split("\0") if n] if out is not None else None


def _dod_files(dod_path, story_id):
    """`<dod_path>/DoD_<id>.md` and `<dod_path>/Decisions_<id>.md` as cwd-relative paths; none for an absolute or `..` dod_path."""
    if not isinstance(dod_path, str) or not dod_path.strip():
        return []
    p = dod_path.strip().replace("\\", "/")
    if p.startswith("/") or ".." in p.split("/"):
        return []
    p = posixpath.normpath(p)
    return [(f"{p}/{n}" if p != "." else n) for n in (f"DoD_{story_id}.md", f"Decisions_{story_id}.md")]


def _spec_tasks(repo, spec_dir, dod_path, head_full):
    """`<spec dir>/tasks.md` (repo-root-relative) when the ledger's untrusted `Spec dir:` is valid in HEAD's tree, else None."""
    try:
        spec = prs.spec_dir_in_tree(spec_dir, dod_path, head_full, str(repo)) if head_full else None
    except prs._Refuse:
        spec = None
    return f"{spec}/tasks.md" if spec else None


def _l1_finding(rule, ac, detail, fix):
    return _finding(rule, f"{ac}: {detail}", fix)


_EVIDENCE_FIX = ("record the commits in the ledger section 3 status cell as `red <sha> → green <sha>`: a red commit with "
                 "only tests, then a green commit with the code")


def check_l1(rows, repo, head, tests, dod_path, spec_dir, story_id, ac_count=0):
    """L1 for each section 3 row (and each AC-1..AC-<ac_count> that has no row). A `detail` names the AC, the rule and
    a count; it echoes at most 12 hex characters of a SHA and never a file name (PR text and git output are untrusted).
    A red commit may change test files and exactly three others: the story's ledger and decisions file and the valid
    spec dir's tasks.md (a tick); never a directory's worth of files."""
    out = []
    head_full = (_out(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{head}^{{commit}}") or b"").decode().strip()
    prefix = (_out(repo, "rev-parse", "--show-prefix") or b"").decode("utf-8", "replace").strip()  # repo below the toplevel
    allowed = {prefix + f for f in _dod_files(dod_path, story_id)} | {_spec_tasks(repo, spec_dir, dod_path, head_full)} - {None}
    is_test = lambda f: any(gate._glob_match(f, g) for g in tests)
    for row in rows:
        ac, ev = row["ac"], parse_evidence(row["status"])
        if ev is None:
            out.append(_l1_finding("evidence-missing", ac, "the status cell has no `red <sha> → green <sha>` pair", _EVIDENCE_FIX))
            continue
        full = {k: _commit(repo, ev[k]) for k in ("red", "green")}
        bad = [k for k in full if full[k] is None]
        for k in bad:
            out.append(_l1_finding("sha-unknown", ac, f"the {k} commit {ev[k][:12].lower()} is not a commit in this repository "
                                   "(or git could not be run)", "use the SHAs of the real red and green commits (`git log`)"))
        if bad:
            continue
        red, green = full["red"], full["green"]
        merges = [k for k in full if _is_merge(repo, full[k])]
        for k in merges:
            out.append(_l1_finding("merge-commit", ac, f"the {k} commit is a merge commit (or its parents could not be read)",
                                   "record plain commits: tests only for red, code for green"))
        if merges:
            continue
        if red == green or not _is_ancestor(repo, red, green):
            why = "red and green are the same commit" if red == green else "the red commit is not an ancestor of the green commit"
            out.append(_l1_finding("red-not-ancestor", ac, why, "commit the failing tests first (red), then the code (green), on one branch"))
        else:
            names, added = _changed(repo, red), _changed(repo, red, "AMT")
            if names is None or added is None:
                out.append(_l1_finding("sha-unknown", ac, "the files of the red commit could not be read", "re-run in a full clone"))
            else:
                code = [f for f in names if not is_test(f) and f not in allowed]
                if code:
                    out.append(_l1_finding("red-touches-code", ac, f"the red commit changes {len(code)} non-test file(s) besides the "
                                           "ledger, decisions file and tasks.md", "move the code into the green commit; red holds tests only "
                                           "(and the ledger, decisions file and tasks.md tick)"))
                if not any(is_test(f) for f in added):
                    out.append(_l1_finding("red-no-test", ac, "the red commit adds or changes no test file",
                                           "the red commit must add the failing test"))
        if not (head_full and _is_ancestor(repo, green, head_full)):
            out.append(_l1_finding("green-not-on-branch", ac, "the green commit is not an ancestor of HEAD" if head_full
                                   else "HEAD does not resolve to a commit", "record a green commit that is on this branch"))
    have = {r["ac"] for r in rows}
    out += [_l1_finding("evidence-missing", f"AC-{n}", "no section 3 row", _EVIDENCE_FIX)
            for n in range(1, ac_count + 1) if f"AC-{n}" not in have]
    return out


def _l1(c):
    if c.ledger is None:
        return []  # reported by _ledger
    tests = {**gate.DEFAULT_TESTS, **c.cfg.gate["tests"]}
    return check_l1(gate.parse_section3(c.ledger), c.repo, "HEAD", tests, c.cfg.dod_path, c.spec_dir,
                    c.id, len(dod.ac_lines(c.ledger)))


# ---- verification record (D§5): written by `verify`, only validated by `check` ----
_V_HEADING = re.compile(r"^### Verification\b[^\r\n]*", re.M)
_V_HEAD = re.compile(r"### Verification — (\d{4}-\d{2}-\d{2}) @ ([0-9a-f]{7,40})")
_V_AC = re.compile(r"- (AC-\d+): red ([0-9a-f]{12}) exit (\d{1,3}) · green ([0-9a-f]{12}) exit (\d{1,3}) · L1 ok")
_V_HEADLINE = re.compile(r"- head: ([0-9a-f]{12}) exit (\d{1,3})")
_SUPERSEDED = " (superseded)"


def _range_findings(rows, repo, base):
    """`not-in-range` when `base` is no commit here, or a red or green commit is already in `base` (not this branch's work)."""
    base_full = (_out(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{base}^{{commit}}") or b"").decode().strip() \
        if base.strip() else ""
    if not base_full:
        return [_finding("not-in-range", "the base ref is not a commit in this repository (or git could not be run)",
                         "pass an existing branch, tag or commit as --base")]
    out = []
    for row in rows:
        for k, sha in (parse_evidence(row["status"]) or {}).items():
            full = _commit(repo, sha)
            if full and _is_ancestor(repo, full, base_full):
                out.append(_l1_finding("not-in-range", row["ac"], f"the {k} commit is already in the base",
                                       "record red and green commits made after the base: this branch's own work"))
    return out


def _same_sha(cell_sha, recorded12):
    n = min(len(cell_sha), 12)
    return cell_sha[:n].lower() == recorded12[:n]


def _record_stale(repo, head, recorded, dod_path, spec_dir, story_id):
    """Why the record (made at the commit `recorded`) no longer covers `head`, or None when it does: head is that commit
    or a descendant whose later commits touch only the ledger, the decisions file and tasks.md."""
    head_full = (_out(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{head}^{{commit}}") or b"").decode().strip()
    rec_full = _commit(repo, recorded)
    if not head_full:
        return "HEAD does not resolve to a commit"
    if rec_full is None:
        return "the commit the record was made at is not in this repository (or git could not be run)"
    if rec_full == head_full:
        return None
    if not _is_ancestor(repo, rec_full, head_full):
        return "the commit the record was made at is not an ancestor of HEAD"
    try:
        if prs.only_ledger_since(str(repo), rec_full, head_full, dod_path, spec_dir, story_id):
            return None
    except prs._Refuse:
        return "git could not compare the record's commit with HEAD"
    return "later commits change files other than the ledger, the decisions file and tasks.md"


def validate_record(ledger_md, rows, repo, head, dod_path, spec_dir, story_id, base=None, squash_ok=False):
    """Findings for the latest live `### Verification` block of ledger section 4 (a block in a fence or comment is not
    one): `record-missing`, `record-stale`, `record-incomplete`, `record-failed`, and `not-in-range` with `base`. It runs
    no test and reads only the ledger and git. `squash_ok` (the `done` phase, after a squash merge the red and green
    commits are no ancestors of HEAD): no staleness or range check, only completeness, SHAs against the status cells and exit codes."""
    redo = f"run `ado strict verify {story_id}` and commit the ledger"
    log = dod._visible(dod.section(ledger_md, 4) or "")[0]
    live = [m for m in _V_HEADING.finditer(log) if not m.group(0).rstrip().endswith(_SUPERSEDED.strip())]
    head_m = _V_HEAD.fullmatch(live[-1].group(0).rstrip()) if live else None
    if head_m is None:
        return [_finding("record-missing", "ledger section 4 has no verification record (a `### Verification — <date> @ <sha>` block)", redo)]
    rest = log[live[-1].end():]
    nxt = re.search(r"^#{1,3} ", rest, re.M)
    found, heads = {}, []
    for line in (rest[:nxt.start()] if nxt else rest).splitlines():
        m = _V_AC.fullmatch(line.strip())
        if m:
            found.setdefault(m[1], []).append(m.groups()[1:])
        if m := _V_HEADLINE.fullmatch(line.strip()):
            heads.append(m.groups())
    out = []
    if not squash_ok:
        if why := _record_stale(repo, head, head_m[2], dod_path, spec_dir, story_id):
            return [_finding("record-stale", f"the verification record is stale: {why}", redo)]
        if base is not None:
            out += _range_findings(rows, repo, base)
    if len(heads) != 1 or not _same_sha(head_m[2], heads[0][0]):
        out.append(_finding("record-incomplete", "the verification record has no single `- head:` line naming the recorded head", redo))
    elif int(heads[0][1]) != 0:
        out.append(_finding("record-failed", "the record shows the tests failing at the recorded HEAD",
                            "fix the code so the tests pass at HEAD, then " + redo))
    for row in rows:
        ac, ev, got = row["ac"], parse_evidence(row["status"]), found.get(row["ac"], [])
        if len(got) != 1:
            out.append(_l1_finding("record-incomplete", ac, "the verification record has no line for it" if not got
                                   else "the verification record has more than one line for it", redo))
        elif ev is None or not (_same_sha(ev["red"], got[0][0]) and _same_sha(ev["green"], got[0][2])):
            out.append(_l1_finding("record-incomplete", ac, "the recorded red and green SHAs differ from the section 3 status cell", redo))
        elif int(got[0][1]) == 0 or int(got[0][3]) != 0:
            out.append(_l1_finding("record-failed", ac, "the record shows a red run that passed or a green run that failed",
                                   "make the red tests fail and the green tests pass, then " + redo))
    return out


def _record(c):
    if c.ledger is None:
        return []  # reported by _ledger
    return validate_record(c.ledger, gate.parse_section3(c.ledger), c.repo, "HEAD", c.cfg.dod_path, c.spec_dir, c.id)


def _record_done(c):
    if c.ledger is None:
        return []
    return validate_record(c.ledger, gate.parse_section3(c.ledger), c.repo, "HEAD", c.cfg.dod_path, c.spec_dir, c.id, squash_ok=True)


PHASES = {
    "implement": [_ledger, _spec_files, _placeholders, _open_questions, _board, _test_command],
    "pr": [_ledger, _l1, _record],
    "done": [_ledger, _l1, _record_done],
}


def check(client, story_id, phase, repo="."):
    if phase not in PHASES:
        raise ValueError(f"unknown phase (valid: {', '.join(PHASES)})")
    mode = client.config.mode
    if mode != "strict":
        return {"phase": phase, "mode": mode, "ok": True, "findings": [], "note": "mode is advisory"}
    ctx = _Ctx(client, story_id, repo)
    findings = [f for rule in PHASES[phase] for f in rule(ctx)]
    return {"phase": phase, "mode": mode, "ok": not findings, "findings": findings}


# ---- L2 test runner (D§5): the project's test command at one commit, in a throwaway checkout ----
# The command is code from the project under test. It runs in a throwaway clone outside the repo with an allow-listed
# environment, stdin closed, a timeout and its own process group; nothing it prints is returned or logged (counts only).
_ENV_ALLOW = ("PATH", "LANG", "TERM", "TMPDIR", "VIRTUAL_ENV", "JAVA_HOME", "GOPATH", "CARGO_HOME", "NODE_PATH")
_ENV_DENY_NAMES = {"ADO_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "AZURE_DEVOPS_EXT_PAT", "SYSTEM_ACCESSTOKEN", "SSH_AUTH_SOCK"}
_ENV_DENY_SUFFIX = ("_TOKEN", "_PAT", "_SECRET", "_KEY", "_PASSWORD", "_CREDENTIALS")


def scrubbed_env(env, home="/nonexistent"):
    """The environment a test run gets: only the allow-list (PATH, LANG, LC_*, TERM, TMPDIR and the toolchain variables
    that are present), then a deny-list pass for secret-looking names (defence in depth). HOME is `home`, never the real one."""
    out = {k: v for k, v in env.items() if k in _ENV_ALLOW or k.startswith("LC_")}
    out = {k: v for k, v in out.items() if not (k.upper() in _ENV_DENY_NAMES or k.upper().startswith("AWS_")
                                                  or k.upper().endswith(_ENV_DENY_SUFFIX))}
    return {**out, "HOME": home, "PYTHONDONTWRITEBYTECODE": "1"}


def _git_env(env, home="/nonexistent"):
    """`scrubbed_env` for every git call and for the test command: no GIT_* from the parent (GIT_INDEX_FILE, GIT_DIR,
    GIT_ASKPASS, GIT_SSH*, GIT_CONFIG_*), no system or global git config, no prompts, no replace refs."""
    return {**scrubbed_env(env, home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1"}


def _kill_group(p):
    # shortcut: after a normal exit the pid is already reaped; its reuse as a group id within microseconds is ignored
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _count_lines(fd, counts, i):
    """Read `fd` in 64 KiB chunks until EOF, keeping only the number of lines (memory stays constant); owns and closes `fd`."""
    n, last = 0, b"\n"
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return
            n, last = n + chunk.count(b"\n"), chunk[-1:]
            counts[i] = n + (last != b"\n")
    except OSError:
        pass
    finally:
        os.close(fd)


def _run_group(command, cwd, env, timeout):
    """(exit or None, timed_out, stdout lines, stderr lines, seconds to the process exit); the whole process group dies on a timeout, an interrupt or exit.
    shortcut: a descendant that calls setsid escapes the group; if it also holds the output pipes the drain gives up after
    10 s (its later output is not counted) and the thread stays until it exits."""
    (r_out, w_out), (r_err, w_err), counts, t0 = os.pipe(), os.pipe(), [0, 0], time.monotonic()
    try:
        try:
            p = subprocess.Popen(["/bin/sh", "-c", command], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=w_out,
                                 stderr=w_err, start_new_session=True)
        finally:
            os.close(w_out)
            os.close(w_err)
    except BaseException:
        os.close(r_out)
        os.close(r_err)
        raise
    readers = [threading.Thread(target=_count_lines, args=(fd, counts, i), daemon=True) for i, fd in enumerate((r_out, r_err))]
    for t in readers:
        t.start()
    timed_out = False
    with p:
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(p)
            p.wait()
        except BaseException:
            _kill_group(p)
            raise
        _kill_group(p)  # background processes the command left behind
    elapsed = time.monotonic() - t0  # not the drain wait below
    end = time.monotonic() + 10
    for t in readers:
        t.join(max(0.0, end - time.monotonic()))
    return (None if timed_out else p.returncode), timed_out, counts[0], counts[1], elapsed


def _rmtree(path):
    """Remove a tree including read-only and mode-000 directories (Go's module cache is 555)."""
    try:
        os.chmod(path, 0o700)
        with os.scandir(path) as it:
            entries = list(it)
    except OSError:
        entries = []
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False):
                _rmtree(e.path)
            else:
                os.unlink(e.path)
        except OSError:
            pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def run_at(repo, sha, command, timeout):
    """Run `command` (a shell string from the project's config, never built from anything else) at commit `sha` in a
    throwaway clone (`git clone --shared --no-checkout`, origin removed, never fetched) that shares no config, hooks or index
    with the user's repo. Every git call and the command get a scrubbed environment. Returns `{"sha", "exit", "timed_out",
    "seconds", "stdout_lines", "stderr_lines"}`; the output itself is dropped so test output cannot leak a secret. Raises
    ValueError (fixed text) when `sha` is not 7-40 hex characters naming a commit here, or the checkout fails. The temp dir
    is always removed."""
    if not (isinstance(sha, str) and re.fullmatch(_HEX, sha)):
        raise ValueError("sha must be 7 to 40 hex characters")
    if not (isinstance(command, str) and command.strip()) or isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise ValueError("a command and a timeout of at least 1 second are required")
    genv = _git_env(os.environ)
    full = _commit(repo, sha, env=genv)
    top = _out(repo, "rev-parse", "--show-toplevel", env=genv)
    if full is None or top is None:
        raise ValueError("the sha is not a commit in this repository (a shallow clone may lack it, or git could not be run)")
    top = os.path.realpath(top.decode("utf-8", "replace").strip())
    tmp = tempfile.mkdtemp(prefix="ado-strict-")
    wt, home = os.path.join(tmp, "wt"), os.path.join(tmp, "home")
    try:
        if os.path.commonpath([os.path.realpath(tmp), top]) == top:
            raise ValueError("the temporary directory must be outside the repository")
        os.mkdir(home)
        no_hooks = ["-c", f"core.hooksPath={os.devnull}"]  # a post-checkout hook would run with the environment
        steps = [(top, "clone", "--shared", "--no-checkout", "--quiet", "--end-of-options", top, wt),
                 (wt, "remote", "remove", "origin"),
                 (wt, "checkout", "--quiet", "--detach", "--end-of-options", f"{full}^{{commit}}")]
        for where, *args in steps:
            r = _git(where, *no_hooks, *args, env=genv, timeout=120)
            if r is None or r.returncode != 0:
                raise ValueError("could not create a temporary checkout of the commit")
        code, timed_out, n_out, n_err, secs = _run_group(command, wt, _git_env(os.environ, home), timeout)
        return {"sha": full, "exit": code, "timed_out": timed_out, "seconds": round(secs, 2),
                "stdout_lines": n_out, "stderr_lines": n_err}
    finally:
        try:
            _rmtree(tmp)
        except RecursionError:  # an absurdly deep tree
            shutil.rmtree(tmp, ignore_errors=True)


def run_configured(cfg, repo, sha):
    """run_at with `strict.test_command` and `strict.test_timeout` from the (already validated) config."""
    return run_at(repo, sha, cfg.strict.get("test_command"), cfg.strict.get("test_timeout"))


# ---- `ado strict verify` (D§5): L1, then the test command at every red and green commit, then the record ----
def _with_record(md, heading, lines):
    """`md` with the earlier live `### Verification` headings of section 4 marked superseded and the new block appended at
    the end of section 4 (before `## 5.`), in the ledger's own line ending. Nothing else changes."""
    span = dod.section_span(md, 4)
    if span is None or dod.unclosed(md):
        raise ValueError("the ledger has no section 4 (`## 4.`), or an unclosed code fence or comment; nothing written")
    start, end = span
    mask = dod._mask(md)  # same length as md: offsets found here slice the original
    for m in reversed([m for m in _V_HEADING.finditer(mask, start, end) if not m.group(0).rstrip().endswith(_SUPERSEDED.strip())]):
        md, end = md[:m.end()] + _SUPERSEDED + md[m.end():], end + len(_SUPERSEDED)
    eol = "\r\n" if "\r\n" in md else "\n"
    before = md[:end]
    pre = "" if before.endswith(("\n\n", "\r\n\r\n")) else eol if before.endswith("\n") else eol * 2
    return before + pre + eol.join([heading, *lines, ""]) + eol + md[end:]


def _write_record(path, repo, old, heading, lines):
    """Append the record to the ledger at `path` through a temp file + os.replace; refuses (nothing written) when the
    ledger changed while the tests ran."""
    new = _with_record(old, heading, lines)
    if gate._read(path, repo, set()) != old:
        raise ValueError("the ledger changed while the tests ran; nothing written, run it again")
    try:
        decisions._atomic_write(Path(path), new)
    except (OSError, UnicodeError) as e:
        raise ValueError(f"cannot write the ledger ({e.__class__.__name__}); nothing written")


def verify(client, story_id, record=True, base=None, repo="."):
    """L1 for every AC, then the test command at each AC's red commit (must exit non-zero) and green commit (must exit 0),
    once per distinct commit. Strict mode only. With `record` and a pass, appends the verification record to ledger
    section 4. Returns `{"mode","ok","findings","recorded",...}`; nothing is returned or logged from the tests' output."""
    mode = client.config.mode
    if mode != "strict":
        return {"mode": mode, "ok": True, "findings": [], "note": "mode is advisory"}
    c = _Ctx(client, story_id, repo)
    rows = gate.parse_section3(c.ledger) if c.ledger is not None else []
    findings = _ledger(c) + _l1(c) + _test_command(c)
    if c.ledger is not None and not rows and not any(f["rule"] == "evidence-missing" for f in findings):
        findings.append(_finding("evidence-missing", "ledger section 3 has no AC row", _EVIDENCE_FIX))
    if base is not None:
        findings += _range_findings(rows, repo, base)
    if findings:
        return {"mode": mode, "ok": False, "findings": findings, "recorded": False}
    head = (_out(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", "HEAD^{commit}") or b"").decode().strip()
    if not head:
        raise ValueError("cannot resolve HEAD to a commit; nothing written")
    runs, timeout = {}, c.cfg.strict.get("test_timeout")  # a failed run is cached too: one failure, not one per AC

    def run(full):
        if full not in runs:
            try:
                runs[full] = run_configured(c.cfg, repo, full)
            except ValueError:
                runs[full] = None
        return runs[full]
    lines = []
    for row in rows:
        ac, ev, shown = row["ac"], parse_evidence(row["status"]), []
        for kind in ("red", "green"):
            full = _commit(repo, ev[kind])
            r = run(full)
            if r is None:
                findings.append(_l1_finding("runner-error", ac, f"the {kind} run could not be started: the checkout of the commit failed "
                                            "or git could not be run", "use a full clone (fetch-depth: 0) with a working git, then re-run"))
            elif r["timed_out"]:
                findings.append(_l1_finding("timeout", ac, f"the {kind} run timed out after {timeout} s",
                                            "make the tests faster, or raise strict.test_timeout in ado.config.json"))
            elif kind == "red" and r["exit"] == 0:
                findings.append(_l1_finding("red-passes", ac, f"the tests pass at the red commit {full[:12]}: a red commit must fail",
                                            "make the red commit's test fail without the code (assert the new behaviour), "
                                            "or correct the red SHA or strict.test_command"))
            elif kind == "green" and r["exit"] != 0:
                findings.append(_l1_finding("green-fails", ac, f"the tests fail at the green commit {full[:12]} (exit {r['exit']})",
                                            "make the green commit pass the test command, or correct the green SHA"))
            else:
                shown.append(f"{kind} {full[:12]} exit {r['exit']}")
        if len(shown) == 2:
            lines.append(f"- {ac}: {' · '.join(shown)} · L1 ok")
    r = run(head)  # the record says `@ <head>`: the tests must pass there too (cached when HEAD is a recorded green)
    if head not in {_commit(repo, parse_evidence(row["status"])["green"]) for row in rows}:  # a green HEAD was judged above
        if r is None:
            findings.append(_finding("runner-error", "the HEAD run could not be started: the checkout of the commit failed or git "
                                     "could not be run", "use a full clone (fetch-depth: 0) with a working git, then re-run"))
        elif r["timed_out"]:
            findings.append(_finding("timeout", f"the HEAD run timed out after {timeout} s",
                                     "make the tests faster, or raise strict.test_timeout in ado.config.json"))
        elif r["exit"] != 0:
            findings.append(_finding("head-fails", f"the tests fail at HEAD {head[:12]} (exit {r['exit']}): a later commit broke them",
                                     "fix the code on this branch so the test command passes at HEAD, then re-run verify"))
    seconds = round(sum(r["seconds"] for r in runs.values() if r), 2)
    out = {"mode": mode, "ok": not findings, "findings": findings, "recorded": False, "runs": len(runs), "seconds": seconds}
    if findings or not record:
        return out
    cmd = hashlib.sha256(c.cfg.strict["test_command"].encode()).hexdigest()[:12]
    _write_record(Path(repo) / dod.dod_file(c.cfg.dod_path, story_id), repo, c.ledger,
                  f"### Verification — {date.today().isoformat()} @ {head[:12]}",
                  [*lines, f"- head: {head[:12]} exit {runs[head]['exit']}", f"- command-sha256: {cmd}", f"- seconds: {seconds}"])
    return {**out, "recorded": True}
