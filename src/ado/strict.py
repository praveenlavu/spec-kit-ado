"""`ado strict check`: the rules of strict SDD+TDD mode (design D§4). Read-only: it never writes and never runs tests.

Each phase is its own list of rule functions in PHASES; a later task appends to a list without touching `check`.
A rule takes a _Ctx and returns findings `{"rule", "detail", "fix"}`. The ledger and the spec dir come from the PR
in CI, so every file is read through gate._read (regular, <= 1 MiB, confined to the repo, never a symlink), and a
`detail` carries only ids, file names and counts: never ledger, spec or ADO text."""
import os
import posixpath
import re
import subprocess
from pathlib import Path

from . import dod, gate, prs, push, questions, tasks
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


def _git(repo, *args):
    """A finished git run (bytes), or None when git is missing, cannot start or runs past 30 s: a failure, never a pass."""
    try:
        return subprocess.run(["git", "--no-pager", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
                              check=False, capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
                              env={**os.environ, **_GIT_ENV})
    except (OSError, subprocess.SubprocessError):
        return None


def _out(repo, *args):
    """stdout (bytes) of a successful git run, else None."""
    r = _git(repo, *args)
    return r.stdout if r is not None and r.returncode == 0 else None


def _commit(repo, sha):
    """Full id of the one commit whose id starts with the hex `sha`, else None. Object lookup only: a branch or tag
    named like the SHA never stands in, and an ambiguous prefix or a non-commit object is refused."""
    out = _out(repo, "rev-parse", f"--disambiguate={sha.lower()}")
    objs = out.decode("ascii", "replace").split() if out is not None else []
    return objs[0] if len(objs) == 1 and _out(repo, "cat-file", "-t", objs[0]) == b"commit\n" else None


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


PHASES = {
    "implement": [_ledger, _spec_files, _placeholders, _open_questions, _board, _test_command],
    "pr": [_ledger, _l1],
    "done": [_ledger, _l1],
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
