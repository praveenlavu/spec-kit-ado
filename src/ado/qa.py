"""`ado qa`: the QA file `<dod_path>/QA_<id>.md` and its coverage check. The file holds only what QA wrote (a test plan and case lines); nothing computed is
ever stored in it, so a QA signature over its bytes cannot be voided by a re-run. `check` traces the cases against the ledger's AC lines and the story's
in-scope business rules and reports ids and line numbers only: the file is untrusted text, never echoed. Local only: no token, no request."""
import os
import re
from pathlib import Path

from . import decisions, dod, rules

MAX_LINE = 2000
E_FILE = "the QA file path is taken by something that is not a plain file (a link or a directory); nothing written"
E_NO_QA = "there is no QA file for this story: run `ado qa <id>` to create it"
E_READ = "cannot read the QA file (a link, a hard link, outside the dod_path, over 1 MiB or not UTF-8)"
E_LEDGER = "cannot read the DoD ledger for this story (missing, a link, outside the dod_path, over 1 MiB or not UTF-8)"
E_SECTION = "the QA file needs exactly one `## QA verification` section"
NOTE = ("A defect QA finds goes back to the team as `/reject signoff-qa <hash>: TC-2, TC-5` on the story (the failing case ids after the colon; `ado approvals check <id> --gate signoff-qa --offline` prints the hash); "
        "this check only traces what the cases cover.")
TEMPLATE = """# QA verification for story {id}

> Written by QA, in QA's own words. Nothing in this file is computed: run `ado qa {id} --check` to see which acceptance criteria and business rules
> no case covers yet. A defect goes back with `/reject signoff-qa <hash>: TC-2, TC-5` (hash from `ado approvals check {id} --gate signoff-qa --offline`).

## Test plan

What will be tested, and how, in plain words.

## QA verification

> One line per case: `- [x] TC-1: what was tested -> pass · covers: AC-1, BR-1201-1 · how: manual`
> The result is pass, fail or blocked; `how` is automated or manual; `covers` lists AC-<n> and BR-<story>-<n> ids.

"""
_ID = r"(?:AC-[1-9][0-9]{0,3}|BR-[1-9][0-9]{0,8}-[1-9][0-9]{0,3})"
_CASE = re.compile(rf"- \[[ xX]\] TC-([1-9][0-9]{{0,3}}): \S(?:.*\S)? -> (pass|fail|blocked) · covers: ({_ID}(?:, {_ID})*) · how: (?:automated|manual)")
_HEADING = re.compile(r"## QA verification[ \t]*")


def qa_file(dod_path, story_id):
    return Path(dod_path) / f"QA_{int(story_id)}.md"


def create(dod_path, story_id):
    """Create the template once (True), or leave an existing regular file alone (False). A link or a directory at the name is refused."""
    path = qa_file(dod_path, story_id)
    if os.path.lexists(path):
        if os.path.islink(path) or not os.path.isfile(path):
            raise ValueError(E_FILE)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    except OSError:
        raise ValueError(E_FILE) from None
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(TEMPLATE.format(id=int(story_id)))
    return True


def parse_cases(md):
    """`(cases, malformed)` of the single `## QA verification` section: well-formed cases as `{"tc", "result", "covers"}` and the 1-based line
    numbers of every line that starts a case (`- [`) but is not one (also a case id used twice, after its first use). Fences and comments are
    masked first, so a case inside one does not count. ValueError(E_SECTION) unless the section occurs once."""
    lines = dod._visible(md.replace("\r\n", "\n").replace("\r", "\n"))[0].split("\n")
    heads = [i for i, ln in enumerate(lines) if _HEADING.fullmatch(ln)]
    if len(heads) != 1:
        raise ValueError(E_SECTION)
    cases, malformed, seen = [], [], set()
    for i in range(heads[0] + 1, len(lines)):
        ln = lines[i]
        if ln.startswith("## "):
            break
        if not ln.startswith("- ["):
            continue
        m = _CASE.fullmatch(ln) if len(ln) <= MAX_LINE else None
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            cases.append({"tc": int(m.group(1)), "result": m.group(2), "covers": m.group(3).split(", ")})
        else:
            malformed.append(i + 1)
    return cases, malformed


def scoped(cfg, story_id, ledger):
    """`(readable, scope, files, parsed)`: the story's in-scope business rules (`rules.scope`, phase `pr`) from the ledger's `Rules from:` files and the
    decisions file. `readable` is False when the header, a rules file or the decisions file cannot be read (nothing is then in scope); `files` and `parsed`
    (the parsed decisions) are what `scope` was computed from, `{}` and None when it was not."""
    empty = {"in_scope": [], "excluded": [], "bad_exclusions": []}
    frm = dod.ledger_rules_from(ledger)
    if not frm["valid"]:
        return False, empty, {}, None
    if not frm["ids"]:
        return True, empty, {}, None
    files = rules.local_files(cfg, ledger, ".")
    dpath = decisions.decisions_file(cfg.dod_path, story_id)
    dmd = rules.read_file(dpath, ".", cfg.dod_path) if os.path.lexists(dpath) else ""
    if dmd is None or any(w not in files or files[w]["invalid"] for w in frm["ids"]):
        return False, empty, files, None
    parsed = decisions.parse_decisions(dmd)
    return True, rules.scope(files, frm["ids"], parsed, "pr"), files, parsed


def check(cfg, story_id):
    path = qa_file(cfg.dod_path, story_id)
    text = rules.read_file(path, ".", cfg.dod_path)
    if text is None:
        raise ValueError(E_READ if os.path.lexists(path) else E_NO_QA)
    ledger = rules.read_file(dod.dod_file(cfg.dod_path, story_id), ".", cfg.dod_path)
    if ledger is None:
        raise ValueError(E_LEDGER)
    cases, malformed = parse_cases(text)
    covered = {c for case in cases for c in case["covers"]}
    findings = [{"finding": "malformed-case", "line": n} for n in malformed]
    findings += [{"finding": "uncovered-ac", "id": f"AC-{n}"} for n in range(1, len(dod.ac_lines(ledger)) + 1) if f"AC-{n}" not in covered]
    readable, scope, _, _ = scoped(cfg, story_id, ledger)
    in_scope = scope["in_scope"]
    findings += [{"finding": "uncovered-rule", "id": r} for r in in_scope if r not in covered]
    findings += [] if readable else [{"finding": "rules-unreadable"}]
    results = {k: sum(c["result"] == k for c in cases) for k in ("pass", "fail", "blocked")}
    return {"id": int(story_id), "file": str(path), "ok": not findings, "cases": len(cases), "results": results, "findings": findings, "note": NOTE}
