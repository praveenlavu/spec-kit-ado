import hashlib
import re

from .dod import DECISIONS_HEADING as HEADING, _visible, section

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
