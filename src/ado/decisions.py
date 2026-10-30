import hashlib
import html
import os
import re
import stat
import tempfile
from pathlib import Path

from . import comments, workitems
from .dod import DECISIONS_HEADING as HEADING, _esc, _section2_at, _text, _visible, section

KINDS = ("decision", "assumption", "override")
_OPEN = "_(open)_"
# `- [ ] D1: <text> → _(open)_ · kind: <k> · why: <reason>`; `- [x] D1: <text> → validated|rejected · by: <role> · note: <text>`
# (either also takes ` · posted: <comment id>:<hash8>` and ` · used: <date>`); lenient: spacing, `->`, `*`/`+` bullet.
_CAND = re.compile(r"^\s*(?:[-*+]\s+)?\[([ xX])\]\s*D(\d+)(.*)$")
_COLON = re.compile(r"\s*:\s*(.*?)\s*$")
_STATUS = re.compile(r"\s*(?:→|->)\s*(_\(open\)_|validated|rejected)(?=\s*(?:·|$))")
_KEYS = ("kind", "why", "by", "note", "posted", "used")
_SEP = re.compile(r"\s*·\s*(?=(?:" + "|".join(_KEYS) + r")\s*:)")
MAX_LINE = 2000  # a longer D line is malformed: the regexes below backtrack quadratically over whitespace runs
_POSTED = re.compile(r"(\d+):([0-9a-f]{8})")


def line_hash(text):
    """First 8 hex of sha256 of the text with whitespace collapsed: an edited line no longer matches its `posted:` marker."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:8]


def _line(raw):
    """One ledger line -> the decision dict, or None when it is a `D` line that breaks the grammar."""
    m = _CAND.match(raw)
    c = _COLON.match(m.group(3)) if m else None
    if not c:
        return None
    done = m.group(1) in "xX"
    rest = c.group(1)
    st = list(_STATUS.finditer(rest))  # the LAST status arrow: a `→` inside the text must not split it
    if not st or (st[-1].group(1) == _OPEN) == done:
        return None
    s = st[-1]
    text, tail = rest[:s.start()].strip(), rest[s.end():]
    first, *parts = _SEP.split(tail)
    kv = {}
    for p in parts:
        k, _, v = p.partition(":")
        if k.strip() in kv:
            return None
        kv[k.strip()] = v.strip()
    kind = kv.get("kind", "decision")
    posted = _POSTED.fullmatch(kv["posted"]) if "posted" in kv else None
    bad = (first.strip() or not text or kind not in KINDS or ("posted" in kv and not posted)
           or (not done and not (kv.get("kind") and kv.get("why")))
           or (done and not kv.get("by")) or (not done and ("by" in kv or "note" in kv)))
    if bad:
        return None
    return {"id": f"D{m.group(2)}", "kind": kind, "text": text, "why": kv.get("why", ""),
            "status": "open" if not done else s.group(1), "by": kv.get("by", ""), "note": kv.get("note", ""),
            "posted": {"comment_id": int(posted.group(1)), "hash": posted.group(2)} if posted else None,
            "used": kv.get("used", "")}


def parse_decisions(ledger_md):
    """Ledger section 2 -> {'open': [...], 'validated': [...], 'rejected': [...], 'unparsed': bool}. Lines inside code
    fences or <!-- --> comments are ignored; the first line of a duplicate id wins. `unparsed` is true when a `D` line
    breaks the grammar (it is left out) or a fence or comment is left open in section 2. No section 2 -> all empty."""
    out = {"open": [], "validated": [], "rejected": []}
    body, out["unparsed"] = _visible(section(ledger_md, 2) or "")
    seen = set()
    for raw in body.splitlines():
        if not _CAND.match(raw):  # linear: only the `- [ ] D<n>` prefix is matched here
            continue
        d = _line(raw) if len(raw) <= MAX_LINE else None
        if d is None:
            out["unparsed"] = True
        elif d["id"] not in seen:
            seen.add(d["id"])
            out[d["status"]].append(d)
    return out


def render_line(d):
    """The exact ledger line for a decision; `parse_decisions` reads it back unchanged."""
    if d["status"] == "open":
        parts = [f"- [ ] {d['id']}: {d['text']} → {_OPEN}", f"kind: {d['kind']}", f"why: {d['why']}"]
    else:
        parts = [f"- [x] {d['id']}: {d['text']} → {d['status']}", f"by: {d['by']}", f"note: {d['note']}".rstrip()]
        if d["kind"] != "decision":  # resolving must not lose that a line was an override or assumption
            parts.append(f"kind: {d['kind']}")
        if d["why"]:
            parts.append(f"why: {d['why']}")
    if d["posted"]:
        parts.append(f"posted: {d['posted']['comment_id']}:{d['posted']['hash']}")
    if d["used"]:
        parts.append(f"used: {d['used']}")
    return " · ".join(parts).rstrip()


# ADO strips HTML comments from comment text, so the marker is visible text (as for questions).
MARKER = "[spec-kit-ado:decisions]"
_HEAD = "Decisions and assumptions made during development; please validate or reject:"
MAX_COMMENT_LINE = 500
_AT = re.compile(r"(?<![\w@])@\w[\w.-]*")  # a typed `@name`; comments only knows the anchor form
_CTRL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_MARK = re.compile(r"\s*·\s*posted\s*:\s*\d+:[0-9a-f]{8}(?=\s*(?:·|$))")


def _scrub(text):
    """Same replacements as `ado comments`: mention anchors and typed `@name` -> `@user`, addresses -> `[email]`.
    An address or @mention typed in a decision is replaced, never refused. Free-text names cannot be detected:
    the preview shows the exact comment before anything is posted."""
    return _AT.sub("@user", comments._EMAIL.sub("[email]", comments._MENTION.sub("@user", text)))


def _comment_line(d):
    """(`D<n> (<kind>): <text> — why: <why>` scrubbed, control characters (ANSI escapes, line separators) turned into
    spaces, capped at MAX_COMMENT_LINE chars with …, was it cut)."""
    text = _scrub(f"{d['id']} ({d['kind']}): {d['text']}" + (f" — why: {d['why']}" if d["why"] else ""))
    text = _CTRL.sub(" ", text)
    return (text[:MAX_COMMENT_LINE - 1] + "…", True) if len(text) > MAX_COMMENT_LINE else (text, False)


def plan_post(client, work_item_id, parsed):
    """Read-only, no request: {'action': none|add, 'html', 'lines': [ids], 'cut': [ids], 'hashes': {id: hash8}}.
    Always a NEW comment (never an edit). Lists every open line that is unposted or whose text no longer
    matches its `posted:` hash; resolved lines are never posted. `client` is unused (kept for the verb signature)."""
    todo = [d for d in parsed["open"] if not d["posted"] or d["posted"]["hash"] != line_hash(d["text"])]
    shown = [_comment_line(d) for d in todo]
    body = "<br>".join([MARKER, _HEAD] + [html.escape(t) for t, _ in shown]) if todo else ""
    return {"action": "add" if todo else "none", "html": body, "lines": [d["id"] for d in todo],
            "cut": [d["id"] for d, (_, cut) in zip(todo, shown) if cut],
            "hashes": {d["id"]: line_hash(d["text"]) for d in todo}}


def render_plan(work_item_id, plan, unparsed=False):
    lines = [f"# Plan: post decisions to story {work_item_id}", "",
             "Action: " + ("ADD a new comment (a decisions comment is never edited)" if plan["action"] == "add"
                           else "no unposted decisions; nothing to post"), ""]
    if unparsed:
        lines += ["Note: a decision line breaks the grammar or section 2 has an unclosed fence or comment; "
                  "those lines are not included.", ""]
    if plan["cut"]:
        lines += [f"Note: {', '.join(plan['cut'])} cut to {MAX_COMMENT_LINE} characters (…).", ""]
    if plan["action"] == "add":
        lines += ["Comment text:", ""] + [f"> {_esc(x)}" for x in _text(plan["html"]).splitlines()] + [""]
        lines += [f"After posting, {', '.join(plan['lines'])} get ` · posted: <comment id>:<hash>` in the ledger.", ""]
    return "\n".join(lines)


def _marked(md, hashes, comment_id):
    """The ledger text with ` · posted: <comment_id>:<hash8>` on exactly the lines in `hashes` ({id: hash8});
    ValueError when one of them is missing or edited since the plan."""
    body = section(md, 2) or ""
    start = md.find(body, _section2_at(md)) if body else -1
    vis = _visible(body)[0].split("\n")
    out, todo = [], dict(hashes)
    for raw, v in zip(body.splitlines(keepends=True), vis):
        text = raw.splitlines()[0]  # splitlines also ends a line at U+2028, \x0c, \x85, \x1c: keep that terminator
        eol = raw[len(text):]
        d = _line(v) if _CAND.match(v) and len(v) <= MAX_LINE else None
        if d and d["id"] in todo:
            if line_hash(d["text"]) != todo[d["id"]]:
                break  # edited since the plan: left in `todo`, so the check below refuses
            del todo[d["id"]]
            if d["posted"]:  # only a parsed marker is replaced; marker-like text inside the decision stays
                last = list(_MARK.finditer(text))[-1]  # the real one is in the tail, after the status arrow
                text = (text[:last.start()] + text[last.end():]).rstrip()
            raw = f"{text} · posted: {comment_id}:{line_hash(d['text'])}{eol}"
        out.append(raw)
    if todo:
        raise ValueError(f"the ledger changed since the plan ({', '.join(sorted(todo))}); nothing posted")
    return md[:start] + "".join(out) + md[start + len(body):]


def _atomic_write(path, text):
    """Write `text` over `path` through a uniquely named temp file in the same directory (mode kept), then os.replace."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def apply_post(client, work_item_id, plan, ledger_path):
    """Post exactly the planned comment once, then mark its lines in the ledger (tmp file + os.replace), re-reading
    the ledger after the post. A no-op for none. The ledger is checked before the post, so a changed line posts nothing."""
    if plan["action"] != "add":
        return {}
    path = Path(ledger_path)

    def read():
        with path.open(encoding="utf-8", newline="") as f:
            return f.read()
    _marked(read(), plan["hashes"], 0)  # refuses before any write
    base = f"/{workitems._proj(client)}/_apis/wit/workItems/{work_item_id}/comments"
    cid = (client.post(f"{base}?api-version={comments.API}", {"text": plan["html"]}) or {}).get("id")
    if cid is None:
        raise ValueError("the comment was posted but ADO returned no comment id; mark the lines by hand")
    try:
        _atomic_write(path, _marked(read(), plan["hashes"], cid))
    except (OSError, ValueError) as e:
        pairs = "; ".join(f"{i}: · posted: {cid}:{h}" for i, h in plan["hashes"].items())
        raise ValueError(f"comment {cid} was posted but the ledger could not be updated ({e}); "
                         f"append to the lines by hand: {pairs}")
    return {"comment_id": cid}


_BY = re.compile(r"[A-Za-z0-9 ]{1,40}")
MAX_NOTE = 300
_MARKUP = re.compile(r"<!--|-->|`{3}|~{3}")  # would open or close a comment or fence in the ledger


def _ids(parsed):
    return {d["id"]: d["status"] for k in ("open", "validated", "rejected") for d in parsed[k]}


def resolve(ledger_md, d_id, status, by, note, force=False):
    """The ledger text with the first `d_id` line rewritten as validated|rejected; its kind, why, posted and used
    markers and its line ending are kept, every other byte is unchanged. A line that is already resolved needs `force`.
    ValueError for a bad status, role or note, an unknown id, a line holding an inline comment, or a result that
    `parse_decisions` would not read back the same over the whole ledger."""
    by, note = by.strip(), note.strip()
    if status not in ("validated", "rejected"):
        raise ValueError("the status must be validated or rejected")
    if not _BY.fullmatch(by):
        raise ValueError("--by must be 1-40 letters, digits or spaces (a role, never a name)")
    if len(note) > MAX_NOTE or _CTRL.search(note) or _MARKUP.search(note):
        raise ValueError(f"--note must be at most {MAX_NOTE} characters with no control characters, line breaks, "
                         "`<!--`, `-->` or code fences")
    body = section(ledger_md, 2) or ""
    start = ledger_md.find(body, _section2_at(ledger_md)) if body else -1
    out, found = [], False
    for raw, v in zip(body.splitlines(keepends=True), _visible(body)[0].split("\n")):
        d = _line(v) if not found and _CAND.match(v) and len(v) <= MAX_LINE else None
        if d and d["id"] == d_id:
            found = True
            if v != raw.splitlines()[0]:
                raise ValueError(f"{d_id} holds an inline <!-- --> comment or fence that a rewrite would drop; edit it by hand")
            if d["status"] != "open" and not force:
                raise ValueError(f"{d_id} is already {d['status']} (by {d['by']}); pass --force to overwrite that verdict")
            new = {**d, "status": status, "by": by, "note": note}
            line = render_line(new)
            if len(line) > MAX_LINE or _line(line) != new:  # over-long, or the note collides with the grammar
                raise ValueError(f"the resolved line would be longer than {MAX_LINE} characters or would not read back "
                                 "(a note must not contain ` · kind:`-style markers or `→ validated`)")
            raw = line + raw[len(raw.splitlines()[0]):]  # splitlines also ends a line at U+2028, \x0c, \x85, \x1c
        out.append(raw)
    if not found:
        raise ValueError(f"no decision {d_id} in the ledger (see `ado decisions <id>`)")
    new_md = ledger_md[:start] + "".join(out) + ledger_md[start + len(body):]
    a, b = ledger_md.splitlines(keepends=True), new_md.splitlines(keepends=True)
    before, after = parse_decisions(ledger_md), parse_decisions(new_md)
    if (len(a) != len(b) or sum(x != y for x, y in zip(a, b)) != 1 or after["unparsed"] != before["unparsed"]
            or _ids(after) != {**_ids(before), d_id: status}):
        raise ValueError(f"the rewrite of {d_id} would change more than that line or break the ledger; nothing written")
    return new_md


def apply_resolve(ledger_path, d_id, status, by, note, force=False):
    """Rewrite one line of the ledger in place (unique temp file + os.replace). Refuses when the ledger changed
    between the read and the write."""
    path = Path(ledger_path)

    def read():
        with path.open(encoding="utf-8", newline="") as f:
            return f.read()
    try:
        md = read()
        new = resolve(md, d_id, status, by, note, force)
        if read() != md:
            raise ValueError("the ledger changed while resolving; nothing written, run it again")
        _atomic_write(path, new)
    except (OSError, UnicodeError) as e:
        raise ValueError(f"cannot update the ledger {path} ({e.__class__.__name__}: {e}); nothing written")
