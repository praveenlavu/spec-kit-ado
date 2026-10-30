"""`ado strict check`: the rules of strict SDD+TDD mode (design D§4). Read-only: it never writes and never runs tests.

Each phase is its own list of rule functions in PHASES; a later task appends to a list without touching `check`.
A rule takes a _Ctx and returns findings `{"rule", "detail", "fix"}`. The ledger and the spec dir come from the PR
in CI, so every file is read through gate._read (regular, <= 1 MiB, confined to the repo, never a symlink), and a
`detail` carries only ids, file names and counts: never ledger, spec or ADO text."""
import re
from pathlib import Path

from . import dod, gate, push, questions, tasks
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


PHASES = {
    "implement": [_ledger, _spec_files, _placeholders, _open_questions, _board, _test_command],
    "pr": [],
    "done": [],
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
