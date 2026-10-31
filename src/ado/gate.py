import ast
import errno
import os
import re
import stat
import subprocess
from pathlib import Path

from .config import ConfigInvalid
from .decisions import effective, has_decision_lines, parse_decisions, supersede_problem
from .dod import _visible, ac_hash, ac_lines, ledger_ac_hash, section, unclosed
from .prs import _AB_REF, _GIT_ENV
from .questions import UNPARSED_NOTE, parse_questions

_PY = r"\bassert\b|pytest\.raises|self\.assert"
_JS = r"\bexpect\(|\bassert\b"
_JVM = r"\bassert|\bverify\("
_CS = r"\bAssert\.|Should\("
_GO = r"\bt\.(Error|Fatal)|\bassert\.|\brequire\."
_APEX = r"System\.assert|\bAssert\."
DEFAULT_TESTS = {
    "**/test_*.py": _PY, "**/*_test.py": _PY,
    "**/*.test.[jt]s": _JS, "**/*.spec.[jt]s": _JS, "**/*.test.[jt]sx": _JS,
    "**/*Test.java": _JVM, "**/*Test.kt": _JVM,
    "**/*Tests.cs": _CS, "**/*Test.cs": _CS,
    "**/*_test.go": _GO,
    "**/*Test.cls": _APEX, "**/*_Test.cls": _APEX,
}
_AC_RE = re.compile(r"^AC-\d+$")


def _cell(cells, i):
    c = cells[i] if len(cells) > i else ""
    return "" if "_(" in c else c  # untouched template placeholder -> empty


def parse_section3(md):
    """Section 3 rows tagged AC-<n> -> [{'ac','tests','evidence','status'}]. Graceful: missing/placeholder -> empty."""
    rows = []
    for line in _visible(section(md, 3) or "")[0].splitlines():  # a table in a fence or comment is an example
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not _AC_RE.match(cells[0]):
            continue
        rows.append({"ac": cells[0],
                     "tests": [t.strip() for t in _cell(cells, 2).split(",") if t.strip()],
                     "evidence": _cell(cells, 3), "status": _cell(cells, 4)})
    return rows


MAX_READ = 1 << 20  # bytes; a larger test file is skipped, not read
SKIPPED_NOTE = "{} file(s) skipped: symlink, outside --src, not a regular file, or over 1 MiB"


def _read(path, root, skipped):
    """Text of a small regular file under `root`, else None (also used for the gate's ledger). The PR controls the tree and gate.tests, so a read is
    a content oracle: a symlink (even one pointing inside root), a path whose real path leaves root, a FIFO or
    device, or a file over MAX_READ is never read; it is counted into the `skipped` set, never named.
    Unreadable or non-UTF-8 files return None uncounted. shortcut: only the final component is O_NOFOLLOW; the
    realpath re-check after open narrows, but cannot close, a swap of a parent directory by a concurrent writer
    (the checkout is static while the gate runs); upgrade to dir-fd openat if the gate ever scans a live tree."""
    try:
        real, top = os.path.realpath(path), os.path.realpath(root)  # resolve and confine BEFORE open: nothing outside is ever opened,
        if not os.path.islink(path) and os.path.commonpath([real, top]) == top:  # and a missing target there counts like an existing one
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)  # NONBLOCK: a FIFO must not hang open()
            with os.fdopen(os.open(path, flags), "rb") as f:
                st = os.fstat(f.fileno())
                if (os.path.realpath(path) == real and stat.S_ISREG(st.st_mode)
                        and st.st_size <= MAX_READ and os.path.samestat(st, os.stat(real))):
                    data = f.read(MAX_READ + 1)
                    if len(data) <= MAX_READ:
                        return data.decode("utf-8")
        skipped.add(os.path.abspath(path))  # a set (deduped); only its size is ever reported
    except OSError as e:
        if e.errno == errno.ELOOP:  # O_NOFOLLOW met a symlink (swapped in after the islink check)
            skipped.add(os.path.abspath(path))
    except (UnicodeDecodeError, ValueError):  # ValueError: commonpath across drives (Windows)
        pass
    return None


_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", "dist", "build", ".specify"}


def _glob_match(path, pattern):
    """Glob -> regex on a POSIX path. `**/` = zero or more dirs, trailing `/**` = anything.
    Right-anchored like PurePath.match (leading dirs tolerated) so abs/cwd-relative paths work."""
    path, i, rx = path.replace("\\", "/"), 0, ""
    while i < len(pattern):
        if pattern.startswith("**/", i):
            rx, i = rx + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            rx, i = rx + ".*", i + 2
        elif pattern[i] == "*":
            rx, i = rx + "[^/]*", i + 1
        elif pattern[i] == "?":
            rx, i = rx + "[^/]", i + 1
        elif pattern[i] == "[" and (j := pattern.find("]", i + 2)) != -1:
            cls = pattern[i + 1:j]
            rx, i = rx + "[" + ("^" + cls[1:] if cls[0] == "!" else cls) + "]", j + 1
        elif pattern[i] == "[":
            raise ValueError("invalid glob in gate.tests")  # unbalanced class; the pattern is config text, never echoed
        else:
            rx, i = rx + re.escape(pattern[i]), i + 1
    try:
        return re.fullmatch("(?:.*/)?" + rx, path) is not None
    except re.error:
        raise ValueError("invalid glob in gate.tests") from None


def valid_glob(pattern):
    """A gate.tests key the matcher accepts: 1-200 chars, no NUL/newline, classes balanced and well-formed."""
    if not pattern or len(pattern) > 200 or "\0" in pattern or "\n" in pattern:
        return False
    try:
        _glob_match("", pattern)
    except ValueError:
        return False
    return True


def discover_tests(src, tests, skipped=None):
    """{path: text} of files under src matching any test glob; vendored dirs pruned, unreadable files skipped,
    symlinks / non-regular / oversize files skipped and counted into `skipped` (see _read)."""
    skipped = set() if skipped is None else skipped
    found = {}
    for g in tests:
        _glob_match("", g)  # fail fast on a malformed glob, even when no files exist
    for root, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            p = Path(root, name)
            full = Path(os.path.abspath(p)).as_posix()
            if any(_glob_match(full, g) for g in tests) and (t := _read(p, src, skipped)) is not None:
                found[str(p)] = t
    return found


def _path_match(fname, path):
    f = fname.replace("\\", "/")
    return f == path or f.endswith("/" + path)


def test_present(test_id, sources):
    """Every segment of the ID must occur in the same file. `path::rest` pins the file by path suffix."""
    path, sep, rest = test_id.partition("::")
    if not sep:
        path, rest = "", test_id
    segs = [s.strip() for part in re.split(r"::| > ", rest) for s in part.split(".") if s.strip()]
    if not segs:
        return False  # nothing to look for (empty id, or `path::` with no name)
    path = path.replace("\\", "/").removeprefix("./")
    if sep and not path:
        return False  # `./::name` names no file
    # whole identifiers only: `test_add` must not be found in `test_add_many`
    rxs = [re.compile(rf"(?<![A-Za-z0-9_]){re.escape(s)}(?![A-Za-z0-9_])") for s in segs]
    return any(
        (not path or _path_match(fname, path)) and all(rx.search(text if path else fname + "\n" + text) for rx in rxs)
        for fname, text in sources.items()
    )


test_present.__test__ = False  # not a pytest test, despite the name


def check_traceability(rows, sources, ac_count=0):
    """ac_count = AC lines in ledger section 1; AC-1..AC-n with no section 3 row are unmapped."""
    have = {r["ac"] for r in rows}
    absent = [f"AC-{n}" for n in range(1, ac_count + 1) if f"AC-{n}" not in have]
    if not rows and not ac_count:
        return {"evaluated": False, "mapped": [], "unmapped": [],
                "note": "no AC-tagged rows in section 3 — insufficient to evaluate traceability"}
    mapped, unmapped = [], []
    for r in rows:
        (mapped if any(test_present(t, sources) for t in r["tests"]) else unmapped).append(r["ac"])
    unmapped += absent
    note = "all AC mapped" if not unmapped else f"{len(unmapped)} AC without a known test"
    if absent:
        note += f" ({', '.join(absent)}: no section 3 row)"
    return {"evaluated": True, "mapped": mapped, "unmapped": unmapped, "note": note}


def scan_assertions(changed, tests):
    """Changed test files (by glob) with no assertion match. File-level, not method-level."""
    gaps = []
    for fname, text in changed.items():
        regexes = [rx for g, rx in tests.items() if _glob_match(fname, g)]
        if regexes and not any(re.search(rx, text) for rx in regexes):
            gaps.append({"file": fname, "issue": "no assertions"})
    return gaps


_ASSERT_CALLS = {"raises", "warns", "fail", "deprecated_call"}


def _callee(call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _has_assertion(fn, helpers=frozenset()):
    """A def whose body (nested statements included) asserts: `assert`, `raise AssertionError`, a call to
    `assert*`/raises/warns/fail (bare or attribute, any import alias of the module), or to a same-module helper that does."""
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assert, ast.Raise)) and (isinstance(n, ast.Assert) or _exc_name(n.exc) == "AssertionError"):
            return True
        if isinstance(n, ast.Call) and (c := _callee(n)) and (
                c.startswith("assert") or c in _ASSERT_CALLS or c in helpers):
            return True
    return False


def _exc_name(e):
    e = e.func if isinstance(e, ast.Call) else e
    return e.id if isinstance(e, ast.Name) else ""


def _py_defs(tree, name, cls):
    """Functions called `name` at module level or in a class body (that class `cls` when given)."""
    scopes = [(None, tree)] + [(n.name, n) for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    return [f for c, sc in scopes if cls is None or c == cls for f in sc.body
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == name]


def check_test_assertions(rows, sources):
    """Per mapped test: Python tests are parsed with ast and flagged when their own body asserts nothing.
    Returns (gaps, notes); other languages stay file-level (scan_assertions) and get one note."""
    gaps, notes, trees, other = [], [], {}, False
    for tid in dict.fromkeys(t for r in rows for t in r["tests"]):
        path, sep, rest = tid.partition("::")
        if not sep:
            path, rest = "", tid
        segs = [s.strip() for part in re.split(r"::| > ", rest) for s in part.split(".") if s.strip()]
        if not segs:
            continue
        path = path.replace("\\", "/").removeprefix("./")
        name, cls = segs[-1], (segs[-2] if len(segs) > 1 else None)
        verdicts, unparsed = [], False
        for f, text in sources.items():
            if not f.endswith(".py") or (path and not _path_match(f, path)) or name not in text:
                continue
            if f not in trees:
                try:
                    trees[f] = ast.parse(text)
                except (SyntaxError, ValueError, RecursionError, MemoryError):
                    trees[f] = None
                    notes.append(f"could not parse {f}; per-test assertion check skipped for it")
            if trees[f] is None:
                unparsed = True
                continue
            helpers = {n.name for n in ast.walk(trees[f])  # one level: defs that assert directly
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _has_assertion(n)}
            verdicts += [_has_assertion(d, helpers) for d in _py_defs(trees[f], name, cls)]
        if verdicts and not any(verdicts):
            gaps.append({"test": tid, "issue": "no assertions"})
        elif not verdicts and not unparsed:
            other = other or test_present(tid, {f: t for f, t in sources.items() if not f.endswith(".py")})
    if other:
        notes.append("per-test assertion checking covers Python only; other languages are checked per file")
    return gaps, notes


UNREACHABLE = "ADO not reachable or no token — drift not checked"


def check_drift(ledger_md, work_item, reason=UNREACHABLE):
    """`reason`: the note when there is no story (offline / ADO unreachable)."""
    recorded = ledger_ac_hash(ledger_md)
    if recorded is None:
        return {"evaluated": False, "drift": False, "note": "no AC hash in the ledger — drift not checked"}
    if work_item is None:
        return {"evaluated": False, "drift": False, "note": reason}
    return {"evaluated": True, "drift": ac_hash(work_item) != recorded}


def missing_evidence(rows):
    return [r["ac"] for r in rows if "green" in r["status"].lower() and not r["evidence"]]


COMMITS_NOT_CHECKED = "commits not checked"


def check_commits(repo, story_id, base=None):
    """(short shas in <base>..HEAD whose message lacks `AB#<story_id>`, note). Merge commits are ignored.
    `base` is a ref (CI passes origin/<target>); default is the origin/HEAD branch. A base that cannot be resolved
    (or starts with `-`) checks nothing and says so in the note: missing information never blocks."""
    def git(*args):  # stdout; None for "not found" (exit 1, no stderr); OSError for a real failure
        r = subprocess.run(["git", "--no-replace-objects", "-C", repo, *args], capture_output=True, timeout=30,
                           check=False, env={**os.environ, **_GIT_ENV})
        err = r.stderr.decode("utf-8", "replace").strip()
        if r.returncode == 1 and not err:
            return None
        if r.returncode:
            raise OSError(err[:200])
        return r.stdout.decode("utf-8", "replace").strip()

    try:
        if base is None:
            ref = git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
            sha = ref and git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}")
            label = "no default branch (origin/HEAD)"
        elif base.startswith("-"):
            return [], f"base {base!r} is not a ref — {COMMITS_NOT_CHECKED}"
        else:
            sha = git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{base}^{{commit}}")
            label = f"base {base} not found"
        if not sha:
            return [], f"{label} — {COMMITS_NOT_CHECKED}"
        log = git("log", "--no-merges", "-z", "--format=%h%x1f%B", f"{sha}..HEAD")
    except (OSError, subprocess.SubprocessError) as e:
        return [], f"git failed ({' '.join(str(e).split())[:100] or type(e).__name__}) — {COMMITS_NOT_CHECKED}"
    out = []
    for rec in filter(None, (log or "").split("\0")):
        sha, _, msg = rec.partition("\x1f")
        if not any(int(m.group(1)) == story_id for m in _AB_REF.finditer(msg)):
            out.append(sha.strip())
    return out, None


LEDGER_UNCLOSED_NOTE = "the ledger has an unclosed code fence or comment — sections after it are not evaluated"

# Every check run_gate can report. config.py rejects gate.block names outside this set, and
# tests/test_config.py pins it to run_gate's output keys, so a new check must be added here too.
GATE_CHECKS = ("ac_traceability", "assertion", "ac_drift", "open_questions", "commits_without_story", "open_decisions")


def run_gate(ledger_md, sources, changed, work_item=None, block=(), tests=None, reason=UNREACHABLE, commits=None,
             skipped=0, decisions_md=None, decisions_unreadable=False):
    """`decisions_md`: text of Decisions_<id>.md, or None when there is no such file; `decisions_unreadable`: the file
    exists but could not be read (symlink, outside --src, oversize, not UTF-8): counted as unresolved, never skipped."""
    rows = parse_section3(ledger_md)
    trace = check_traceability(rows, sources, len(ac_lines(ledger_md)))
    gaps = scan_assertions(changed, DEFAULT_TESTS if tests is None else tests)
    test_gaps, notes = check_test_assertions(rows, sources)
    gaps += test_gaps
    drift = check_drift(ledger_md, work_item, reason)
    # An AC row with no test, or none that exists, fails; no rows at all stays "not evaluated".
    qs = parse_questions(ledger_md)
    open_q = [q["id"] for q in qs["open"]]
    ds = parse_decisions(decisions_md or "")  # open and rejected are both unresolved (a decision a live `supersedes` answers is not); reported only when the file has D lines
    eff = effective(ds)
    open_d = [d["id"] for d in sorted(eff["open"] + eff["rejected"], key=lambda d: int(d["id"][1:]))]
    open_d += ["?"] * (ds["unparsed"] or decisions_unreadable or supersede_problem(ds))  # fail closed: a malformed D line, an unclosed fence or comment, an unreadable file, a bad supersedes
    no_story, commits_note = commits or ([], None)
    open_ledger = unclosed(ledger_md)  # fail closed: counts as an open-questions finding, so `gate.block` can stop on it
    found = {"ac_traceability": trace["unmapped"], "assertion": gaps, "ac_drift": drift["drift"],
             "open_questions": open_q or open_ledger, "commits_without_story": no_story, "open_decisions": open_d}
    blocked_by = [k for k in found if k in block and found[k]]
    return {"traceability": trace, "assertion_gaps": gaps, "assertion_notes": notes, "drift": drift,
            "missing_evidence": missing_evidence(rows), "open_questions": open_q,
            "commits_without_story": no_story, **({"open_decisions": open_d} if open_d or has_decision_lines(decisions_md or "") else {}),
            **({"commits_note": commits_note} if commits_note else {}),
            **({"skipped_note": SKIPPED_NOTE.format(skipped)} if skipped else {}),
            **({"open_questions_note": UNPARSED_NOTE} if qs["unparsed"] else {}),
            **({"ledger_unclosed": LEDGER_UNCLOSED_NOTE} if open_ledger else {}), "blocked_by": blocked_by, "blocking": bool(blocked_by)}


def _code(s, cap=200):
    """Untrusted text as one inline-code span: no backticks or newlines, length-capped."""
    return "`" + (" ".join(str(s).replace("`", "'").split())[:cap] or "?") + "`"


TARGET_CONFIG_NOTE = "target branch (strict)"  # `result["config"]` when CI judged the PR with the target branch's config
PR_CONFIG_NOTE = "pull request"  # ... or with the PR's own strict config, when `--base` was given
TRUST_NOTE = "trust-introducing PR: review ado.stack.json as code"  # this PR edits the profile, the lockfile or strict.test_command


def render_summary(result):
    """Markdown PR comment for a gate result (what `--summary-out` writes)."""
    head = "### ADO gate\n\n"
    source = f"config: {result['config']}\n" if result.get("config") in (TARGET_CONFIG_NOTE, PR_CONFIG_NOTE) else ""
    source += f"{TRUST_NOTE}\n" if result.get("trust_note") == TRUST_NOTE else ""
    if "traceability" not in result:  # no / unreadable ledger: advisory note only
        return head + f"Advisory: {_code(result.get('note', ''))}\n" + source
    trace, drift = result["traceability"], result["drift"]
    drift_line = ("AC changed in ADO since the ledger was seeded" if drift["drift"]
                  else "no drift" if drift["evaluated"] else drift.get("note", "drift not checked"))
    lists = [("ac_traceability", "AC without a known test", trace["unmapped"]),
             ("assertion", "Test files or mapped tests with no assertions",
              [g.get("file") or g["test"] for g in result["assertion_gaps"]]),
             ("evidence", "Green AC without evidence", result["missing_evidence"]),
             ("open_questions", "Open questions in ledger section 2", result.get("open_questions", [])),
             ("open_decisions", "Open decisions in the decisions file", result.get("open_decisions", []))]
    found = {k: f"- {label}: {', '.join(map(_code, v))}" for k, label, v in lists if v}
    if cs := result.get("commits_without_story"):
        more = [f"…and {len(cs) - 20} more"] if len(cs) > 20 else []
        found["commits_without_story"] = ("- Commits without the story's `AB#<id>` reference: "
                                          + ", ".join([*map(_code, cs[:20]), *more]))
    if note := result.get("ledger_unclosed"):  # stands in for the open-questions line when that list is empty
        found["open_questions" if "open_questions" not in found else "ledger_unclosed"] = f"- {_code(note)}"
    if not trace["evaluated"]:
        found["trace_note"] = "- AC traceability not evaluated (no AC-tagged rows in section 3)"
    blocked = [found.pop(k, f"- {drift_line}") for k in result.get("blocked_by", [])]
    out = []
    if blocked:
        out += ["**Blocking - this check fails the PR:**", *blocked, ""]
    if found:
        out += ["**Advisory:**", *found.values(), ""]
    if not blocked and not found:
        out += ["No findings.", ""]
    tail = [f"AC drift: {drift_line}"]
    if result.get("open_decisions") == []:
        tail.append("Open decisions: none")
    if note := result.get("open_questions_note"):
        tail.append("Open questions: " + _code(note))
    if note := result.get("commits_note"):
        tail.append("Commit check: " + _code(note))
    if note := result.get("skipped_note"):
        tail.append("Files skipped: " + _code(note))
    if notes := result.get("assertion_notes"):
        tail.append("Assertion check: " + "; ".join(map(_code, notes)))  # informational, not a finding
    return head + "\n".join(out + tail) + "\n" + source


def render_error(reason):
    """Markdown body for a gate that could not run (or, for an invalid config, FAILED). The $ADO_TOKEN value is redacted before capping."""
    invalid = isinstance(reason, ConfigInvalid)
    reason = " ".join(str(reason).split())
    if token := os.environ.get("ADO_TOKEN", "").strip():
        reason = reason.replace(token, "***")
    if invalid and str(reason).startswith(("ado.stack.json at the target branch", "ado.kits.lock.json at the target branch")):
        return f"### ADO gate\n\n**ADO gate FAILED: target branch trust files are invalid:** {_code(reason, 500)}\n\nFix the file on the target branch; an invalid one fails this check.\n"
    if invalid:
        return f"### ADO gate\n\n**ADO gate FAILED: ado.config.json is invalid:** {_code(reason, 500)}\n\nFix the config; an invalid config fails this check.\n"
    return f"### ADO gate\n\nThe gate could not run: {_code(reason, 500)}\n\nThis check is advisory and does not fail the PR.\n"
