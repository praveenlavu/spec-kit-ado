import hashlib
import html
import os
import re
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
        parts = [f"- [x] {d['id']}: {d['text']} → {d['status']}", f"by: {d['by']}", f"note: {d['note']}"]
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
    tmp = path.with_name(path.name + ".tmp")
    try:
        fresh = _marked(read(), plan["hashes"], cid)
        with tmp.open("w", encoding="utf-8", newline="") as f:
            f.write(fresh)
        os.replace(tmp, path)
    except (OSError, ValueError) as e:
        tmp.unlink(missing_ok=True)
        pairs = "; ".join(f"{i}: · posted: {cid}:{h}" for i, h in plan["hashes"].items())
        raise ValueError(f"comment {cid} was posted but the ledger could not be updated ({e}); "
                         f"append to the lines by hand: {pairs}")
    return {"comment_id": cid}
