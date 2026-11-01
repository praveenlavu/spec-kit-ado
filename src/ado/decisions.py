import hashlib
import html
import os
import re
import stat
import tempfile
from pathlib import Path

from . import comments, workitems
from .dod import _esc, _text, _visible

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
_SUP = re.compile(r"supersedes D([0-9]{1,6}): ")  # the only place a supersedes relation is read from: the start of the text
SUP_HIDDEN = re.compile(r"\bD\d+(?!\d)\s*:\s*supersedes\s+D\d")  # the same words on a line that is not a decision line


def decisions_file(dod_dir, story_id):
    """`<dod_path>/Decisions_<id>.md`: every assumption and decision of a story, one `D<n>` line each."""
    return Path(dod_dir) / f"Decisions_{story_id}.md"


def render_file(story_id):
    """A new decisions file: a title and a format note. The note is a code span inside a paragraph, so it does not
    parse as a decision line (`parse_decisions` only reads lines that start with a checkbox)."""
    return (f"# Decisions — US{story_id}\n\n"
            "Format, one line each: `- [ ] D1: <text> → _(open)_ · kind: decision · why: <reason>`; "
            "a human records the outcome.\n\n")


def line_hash(text):
    """First 8 hex of sha256 of the text with whitespace collapsed: an edited line no longer matches its `posted:` marker."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:8]


def marker_hash(d):
    """The hash a `posted:` marker carries for the parsed decision `d`: the text alone, but for an override or a superseder the
    id, the text and the reason (what the PO read): an edited reason or a marker copied from another line no longer matches."""
    return line_hash(f"{d['id']}|{d['text']}|{d['why']}" if d["kind"] == "override" or d.get("supersedes") else d["text"])


_STATES = ("open", "validated", "rejected")


def _answers(parsed):
    """(entries by id, {target: [superseder ids]} over the valid edges, a bad edge was seen, {id: stands}). An edge is bad when its
    target is unknown or an override, or its superseder id is not higher (that covers self-reference and cycles); a bad edge
    changes nothing here. A decision `stands` unless it is rejected and no decision that stands supersedes it: the head of a chain
    answers everything below it."""
    ents = {d["id"]: d for k in _STATES for d in parsed[k]}
    by, bad = {}, False
    for d in ents.values():
        t = d.get("supersedes")
        if t is None:
            continue
        if t not in ents or ents[t]["kind"] == "override" or int(t[1:]) >= int(d["id"][1:]):
            bad = True
        else:
            by.setdefault(t, []).append(d["id"])
    stands = {}
    for i in sorted(ents, key=lambda i: -int(i[1:])):  # a superseder has the higher id, so it is decided first
        stands[i] = ents[i]["status"] != "rejected" or any(stands[s] for s in by.get(i, ()))
    return ents, by, bad, stands


def supersede_problem(parsed):
    """True when a `supersedes D<n>: ` line is invalid: unknown target, an override as target, a superseder that is not
    higher than its target (self-reference, a cycle). Strict mode reports it as `decisions-invalid`, the gate fails closed."""
    return _answers(parsed)[2]


def _stale(d):
    return not d["posted"] or d["posted"]["hash"] != marker_hash(d)


def supersede_unposted(parsed):
    """The decisions in a supersedes relation the PO has not seen as they read, by id order: every line that supersedes another
    (whatever its status or kind) and every open line an effective superseder drops, each without a `posted:` marker or with one
    that no longer matches `marker_hash`. The marker is a local claim, never checked against ADO."""
    kept = {d["id"] for d in effective(parsed)["open"]}
    out = [d for k in _STATES for d in parsed[k] if _stale(d) and (d.get("supersedes") or (k == "open" and d["id"] not in kept))]
    return sorted(out, key=lambda d: int(d["id"][1:]))


def masked_by(parsed, d_id):
    """The id of the decision that stands as superseder of `d_id` (so its own verdict is not counted now), else None."""
    _, by, _, stands = _answers(parsed)
    return next((s for s in by.get(d_id, ()) if stands[s]), None)


def effective(parsed):
    """`parsed` without the decisions a live superseder answers (a superseded line stays in the file, only the set the gates
    count changes). A superseder is live when it is not rejected, or when it is itself answered by a live one; a rejected
    superseder with nothing live above it re-activates its target. Invalid edges drop nothing."""
    _, by, _, stands = _answers(parsed)
    gone = {t for t, ss in by.items() if any(stands[s] for s in ss)}
    return {**{k: [d for d in parsed[k] if d["id"] not in gone] for k in _STATES}, "unparsed": parsed["unparsed"]}


def unposted(parsed):
    """The lines the PO has not seen as they now read: the open lines, then the override lines that are open or validated
    (a validated override still has to reach the thread), each without a `posted:` marker or with one that no longer matches.
    An open line a superseder answers stays in: the PO must see it before it is retired. One predicate for `plan_post` and the
    strict `override-unposted` rule."""
    lines = parsed["open"] + [d for d in parsed["validated"] if d["kind"] == "override"]
    return [d for d in lines if not d["posted"] or d["posted"]["hash"] != marker_hash(d)]


def _line(raw):
    """One file line -> the decision dict, or None when it is a `D` line that breaks the grammar."""
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
    sup = _SUP.match(text)
    return {"id": f"D{m.group(2)}", "kind": kind, "text": text, "why": kv.get("why", ""),
            "status": "open" if not done else s.group(1), "by": kv.get("by", ""), "note": kv.get("note", ""),
            "posted": {"comment_id": int(posted.group(1)), "hash": posted.group(2)} if posted else None,
            "used": kv.get("used", ""), **({"supersedes": f"D{sup.group(1)}"} if sup else {})}


def has_decision_lines(md):
    """True when the decisions file has a `D<n>` checkbox line outside fences and comments, well-formed or not."""
    return any(_CAND.match(ln) for ln in _visible(md)[0].splitlines())


def parse_decisions(md):
    """The decisions file -> {'open': [...], 'validated': [...], 'rejected': [...], 'unparsed': bool}. Lines inside code
    fences or <!-- --> comments are ignored; the first line of a duplicate id wins. `unparsed` is true when a `D` line
    breaks the grammar (it is left out) or a fence or comment is left open. A text that starts with `supersedes D<n>: `
    adds `supersedes: "D<n>"` to its entry (no other entry has the key). Empty text -> all empty."""
    out = {"open": [], "validated": [], "rejected": []}
    body, out["unparsed"] = _visible(md)
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
    """The exact file line for a decision; `parse_decisions` reads it back unchanged."""
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
_AT = comments._AT  # a typed `@name`
_CTRL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_MARK = re.compile(r"\s*·\s*posted\s*:\s*\d+:[0-9a-f]{8}(?=\s*(?:·|$))")


def _scrub(text):
    """Same replacements as `ado comments`: mention anchors and typed `@name` -> `@user`, addresses -> `[email]`.
    An address or @mention typed in a decision is replaced, never refused. Free-text names cannot be detected:
    the preview shows the exact comment before anything is posted."""
    return _AT.sub("@user", comments.scrub_emails(comments.scrub_mentions(text)))


def _comment_line(d):
    """(`D<n> (<kind>): <text> — why: <why>` scrubbed, control characters (ANSI escapes, line separators) turned into
    spaces, capped at MAX_COMMENT_LINE chars with …, was it cut)."""
    text = _scrub(f"{d['id']} ({d['kind']}): {d['text']}" + (f" — why: {d['why']}" if d["why"] else ""))
    text = _CTRL.sub(" ", text)
    return (text[:MAX_COMMENT_LINE - 1] + "…", True) if len(text) > MAX_COMMENT_LINE else (text, False)


def plan_post(client, work_item_id, parsed):
    """Read-only, no request: {'action': none|add, 'html', 'lines': [ids], 'cut': [ids], 'hashes': {id: hash8}}.
    Always a NEW comment (never an edit). Lists every open line that is unposted or whose text no longer
    matches its `posted:` hash (`unposted`); other resolved lines are never posted. `client` is unused (kept for the verb signature)."""
    todo = unposted(parsed)
    shown = [_comment_line(d) for d in todo]
    body = "<br>".join([MARKER, _HEAD] + [html.escape(t) for t, _ in shown]) if todo else ""
    return {"action": "add" if todo else "none", "html": body, "lines": [d["id"] for d in todo],
            "cut": [d["id"] for d, (_, cut) in zip(todo, shown) if cut],
            "hashes": {d["id"]: marker_hash(d) for d in todo}}


def render_plan(work_item_id, plan, unparsed=False):
    lines = [f"# Plan: post decisions to story {work_item_id}", "",
             "Action: " + ("ADD a new comment (a decisions comment is never edited)" if plan["action"] == "add"
                           else "no unposted decisions; nothing to post"), ""]
    if unparsed:
        lines += ["Note: a decision line breaks the grammar or the decisions file has an unclosed fence or comment; "
                  "those lines are not included.", ""]
    if plan["cut"]:
        lines += [f"Note: {', '.join(plan['cut'])} cut to {MAX_COMMENT_LINE} characters (…).", ""]
    if plan["action"] == "add":
        lines += ["Comment text:", ""] + [f"> {_esc(x)}" for x in _text(plan["html"]).splitlines()] + [""]
        lines += [f"After posting, {', '.join(plan['lines'])} {'gets' if len(plan['lines']) == 1 else 'get'} ` · posted: <comment id>:<hash>` in the decisions file.", ""]
    return "\n".join(lines)


def _marked(md, hashes, comment_id):
    """The decisions file text with ` · posted: <comment_id>:<hash8>` on exactly the lines in `hashes` ({id: hash8});
    ValueError when one of them is missing or edited since the plan."""
    out, todo = [], dict(hashes)
    for raw, v in zip(md.splitlines(keepends=True), _visible(md)[0].split("\n")):
        text = raw.splitlines()[0]  # splitlines also ends a line at U+2028, \x0c, \x85, \x1c: keep that terminator
        eol = raw[len(text):]
        d = _line(v) if _CAND.match(v) and len(v) <= MAX_LINE else None
        if d and d["id"] in todo:
            if marker_hash(d) != todo[d["id"]]:
                break  # edited since the plan: left in `todo`, so the check below refuses
            del todo[d["id"]]
            if d["posted"]:  # only a parsed marker is replaced; marker-like text inside the decision stays
                last = list(_MARK.finditer(text))[-1]  # the real one is in the tail, after the status arrow
                text = (text[:last.start()] + text[last.end():]).rstrip()
            raw = f"{text} · posted: {comment_id}:{marker_hash(d)}{eol}"
        out.append(raw)
    if todo:
        raise ValueError(f"the decisions file changed since the plan ({', '.join(sorted(todo))}); nothing posted")
    return "".join(out)


def _atomic_write(path, text):
    """Write `text` over `path` through a uniquely named temp file in the same directory (mode kept, 0644 for a new
    file), then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def apply_post(client, work_item_id, plan, file_path):
    """Post exactly the planned comment once, then mark its lines in the decisions file (tmp file + os.replace),
    re-reading the file after the post. A no-op for none. The file is checked before the post, so a changed line
    posts nothing."""
    if plan["action"] != "add":
        return {}
    path = Path(file_path)

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
        raise ValueError(f"comment {cid} was posted but the decisions file could not be updated ({e}); "
                         f"append to the lines by hand: {pairs}")
    return {"comment_id": cid}


_BY = re.compile(r"[A-Za-z0-9 ]{1,40}")
MAX_NOTE = 300
_MARKUP = re.compile(r"<!--|-->|`{3}|~{3}")  # would open or close a comment or fence in the decisions file


def _ids(parsed):
    return {d["id"]: d["status"] for k in ("open", "validated", "rejected") for d in parsed[k]}


def resolve(md, d_id, status, by, note, force=False):
    """The file text with the first `d_id` line rewritten as validated|rejected; its kind, why, posted and used
    markers and its line ending are kept, every other byte is unchanged. A line that is already resolved needs `force`.
    ValueError for a bad status, role or note, an unknown id, a line holding an inline comment, or a result that
    `parse_decisions` would not read back the same over the whole file."""
    by, note = by.strip(), note.strip()
    if status not in ("validated", "rejected"):
        raise ValueError("the status must be validated or rejected")
    if not _BY.fullmatch(by):
        raise ValueError("--by must be 1-40 letters, digits or spaces (a role, never a name)")
    if len(note) > MAX_NOTE or _CTRL.search(note) or _MARKUP.search(note):
        raise ValueError(f"--note must be at most {MAX_NOTE} characters with no control characters, line breaks, "
                         "`<!--`, `-->` or code fences")
    out, found = [], False
    for raw, v in zip(md.splitlines(keepends=True), _visible(md)[0].split("\n")):
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
        raise ValueError(f"no decision {d_id} in the decisions file (see `ado decisions <id>`)")
    new_md = "".join(out)
    a, b = md.splitlines(keepends=True), new_md.splitlines(keepends=True)
    before, after = parse_decisions(md), parse_decisions(new_md)
    if (len(a) != len(b) or sum(x != y for x, y in zip(a, b)) != 1 or after["unparsed"] != before["unparsed"]
            or _ids(after) != {**_ids(before), d_id: status}):
        raise ValueError(f"the rewrite of {d_id} would change more than that line or break the file; nothing written")
    return new_md


def _read(path):
    with path.open(encoding="utf-8", newline="") as f:
        return f.read()


def apply_resolve(file_path, d_id, status, by, note, force=False):
    """Rewrite one line of the decisions file in place (unique temp file + os.replace). Refuses when the file changed
    between the read and the write."""
    path = Path(file_path)
    try:
        md = _read(path)
        new = resolve(md, d_id, status, by, note, force)
        if _read(path) != md:
            raise ValueError("the decisions file changed while resolving; nothing written, run it again")
        _atomic_write(path, new)
    except (OSError, UnicodeError) as e:
        raise ValueError(f"cannot update the decisions file {path} ({e.__class__.__name__}: {e}); nothing written")


MAX_TEXT = 1500
_ADD_KINDS = ("decision", "assumption")  # `override` is only created by `add_override` (the strict override command)


def _supersedes_target(md, target):
    """Refuse (ValueError, fixed texts) unless `target` is a decision of `md` the developer may answer: it exists, is not an
    override, is rejected or open (not validated), and no live decision answers it yet."""
    if not re.fullmatch(r"D[0-9]{1,6}", target if isinstance(target, str) else ""):
        raise ValueError("--supersedes must be D<n>: the letter D and one to six digits")
    ents, by, _, stands = _answers(parse_decisions(md))
    if target not in ents:
        raise ValueError("--supersedes names a decision that is not in the decisions file")
    if ents[target]["kind"] == "override":
        raise ValueError("--supersedes cannot answer an override")
    if ents[target]["status"] == "validated":
        raise ValueError("--supersedes can only answer a rejected or open decision, not a validated one")
    if ents[target]["status"] == "open" and _stale(ents[target]):
        raise ValueError(f"post {target} first (`ado decisions <id> --post`): the PO has not seen it, so it cannot be superseded")
    live = [s for s in by.get(target, ()) if stands[s]]
    if live:
        raise ValueError(f"{target} is already superseded by {live[0]}: supersede {live[0]} instead, or have the PO reject it first")


def add(md, kind, text, why="", _kinds=_ADD_KINDS, _min_why=0, supersedes=None):
    """(the file text with one new open `D<n>` line appended, "D<n>"). n = highest id in the file + 1 (a malformed or
    duplicate line still counts), grammar-exact. @mentions and addresses are replaced as in a posted comment.
    ValueError (never echoing the text) for a bad kind, a text of 0 or over MAX_TEXT characters, a why over MAX_NOTE,
    control characters, line breaks, `<!--`, `-->`, code fences, or a line that would not read back (so a file with an
    unclosed fence or comment, which would hide the new line, is refused too). `supersedes` ("D<n>"): the line's text then
    starts with `supersedes D<n>: ` (see `_supersedes_target` for what is refused); the prefix cannot be typed by hand."""
    if kind not in _kinds:
        raise ValueError("--kind must be decision or assumption")
    for name, v in (("the text", text), ("--why", why)):  # before strip(), which would drop a trailing line break
        if _CTRL.search(v) or _MARKUP.search(v):
            raise ValueError(f"{name} must not contain control characters, line breaks, `<!--`, `-->` or code fences")
    text, why = text.strip(), why.strip()
    if kind != "override":  # an override reason is refused for markup, never rewritten
        text, why = _scrub(text), _scrub(why)
    why = why or "unspecified"
    if not 1 <= len(text) <= MAX_TEXT:
        raise ValueError(f"the text must be 1 to {MAX_TEXT} characters")
    if not _min_why <= len(why) <= MAX_NOTE:
        raise ValueError(f"--why must be {f'{_min_why} to ' if _min_why else 'at most '}{MAX_NOTE} characters")
    if _SUP.match(text):
        raise ValueError("the text must not start with `supersedes D<n>: `; use --supersedes D<n>")
    if supersedes is not None:
        _supersedes_target(md, supersedes)
        text = f"supersedes {supersedes}: {text}"
    nums = [int(n) for n in re.findall(r"(?m)^[ \t]*(?:<!--[ \t]*)?(?:[-*+][ \t]+)?\[[ xX]\][ \t]*D(\d+)", md)]  # raw text: an id inside a comment or fence is taken too
    d_id = f"D{max(nums, default=0) + 1}"
    new = {"id": d_id, "kind": kind, "text": text, "why": why, "status": "open", "by": "", "note": "",
           "posted": None, "used": "", **({"supersedes": supersedes} if supersedes is not None else {})}
    line = render_line(new)
    eol = "\r\n" if "\r\n" in md else "\n"
    new_md = md + ("" if not md or md.endswith(("\n", "\r")) else eol) + line + eol
    before, after = parse_decisions(md), parse_decisions(new_md)
    if len(line) > MAX_LINE or _line(line) != new or _ids(after) != {**_ids(before), d_id: "open"} \
            or after["unparsed"] != before["unparsed"]:
        raise ValueError("the line would not read back from the decisions file (a text or --why holding ` · kind:`-style "
                         "markers or `→ validated`, or an unclosed code fence or comment in the file); nothing written")
    return new_md, d_id


def _apply(file_path, story_id, build):
    # shortcut: two concurrent writers can lose a line (no lock); fine for a local human/agent command, add a lock if parallel agents write
    """Append the line `build(md) -> (new md, id)` makes, creating the file from `render_file` when it is missing (unique
    temp file + os.replace). Refuses when the file changed or appeared between the read and the write, and when the file
    on disk does not read back as exactly the new text (the old text is then put back). Returns the new id."""
    path = Path(file_path)
    try:
        exists = path.exists()
        md = _read(path) if exists else render_file(story_id)
        new, d_id = build(md)
        if path.exists() != exists or (exists and _read(path) != md):
            raise ValueError("the decisions file changed while adding; nothing written, run it again")
        _atomic_write(path, new)
        if _read(path) != new:
            if exists:
                _atomic_write(path, md)
            raise ValueError("the decisions file did not read back as written; nothing recorded")
    except (OSError, UnicodeError) as e:
        raise ValueError(f"cannot update the decisions file {path} ({e.__class__.__name__}: {e}); nothing written")
    return d_id


def apply_add(file_path, story_id, kind, text, why="", supersedes=None):
    return _apply(file_path, story_id, lambda md: add(md, kind, text, why, supersedes=supersedes))


OVERRIDE_RULES = ("test-command", "l2", "open-question", "placeholder")  # the strict rules an override may skip
MIN_REASON = 20


def add_override(md, rule, reason):
    """(the file text with one new open `kind: override` line `skip <rule>` whose why is `reason`, "D<n>"), as `add`
    (same grammar, read-back and refusals) but the reason (MIN_REASON..MAX_NOTE characters once stripped) is stored as
    typed: markup is refused, nothing is scrubbed (the posted comment scrubs it like any line)."""
    if rule not in OVERRIDE_RULES:
        raise ValueError(f"the rule must be one of: {', '.join(OVERRIDE_RULES)}")
    if not isinstance(reason, str):
        raise ValueError("--reason is required")
    if _scrub(reason) != reason:
        raise ValueError("--reason must not contain an e-mail address or an @mention (a posted decision replaces them): describe it without")
    parsed = parse_decisions(md)
    live = [d for d in parsed["open"] + parsed["validated"] if d["kind"] == "override" and d["text"] == f"skip {rule}"]
    if live:
        raise ValueError(f"an override of {rule} is already in force ({live[0]['id']}); reject it first to record a new one")
    try:
        return add(md, "override", f"skip {rule}", reason, ("override",), MIN_REASON)
    except ValueError as e:
        raise ValueError(str(e).replace("--why", "--reason")) from None


def apply_override(file_path, story_id, rule, reason):
    return _apply(file_path, story_id, lambda md: add_override(md, rule, reason))


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def stamp_used(md, ids, date):
    """The file text with ` · used: <date>` appended to the lines of the decisions in `ids`, once: a line that already
    has `used` keeps its date. Pure: the caller writes it (`_atomic_write`). Every other byte, line endings included,
    is unchanged. ValueError (nothing returned) for a bad date, an id that is not a line of the file, or a line holding
    an inline comment or fence that a rewrite would drop."""
    if not isinstance(date, str) or not _DATE.fullmatch(date):
        raise ValueError("the date must be YYYY-MM-DD")
    out, todo = [], set(ids)
    for raw, v in zip(md.splitlines(keepends=True), _visible(md)[0].split("\n")):
        text = raw.splitlines()[0]  # the terminator may be U+2028 etc.: keep it as it is
        d = _line(v) if _CAND.match(v) and len(v) <= MAX_LINE else None
        if d and d["id"] in todo:
            todo.discard(d["id"])
            if v != text:
                raise ValueError(f"{d['id']} holds an inline <!-- --> comment or fence that a rewrite would drop; edit it by hand")
            if not d["used"]:
                raw = f"{text} · used: {date}{raw[len(text):]}"
        out.append(raw)
    if todo:
        raise ValueError(f"no such decision in the decisions file: {', '.join(sorted(todo))}")
    return "".join(out)


def apply_stamp(file_path, story_id, ids):
    """Stamp `used: <today>` on the override lines in `ids` (an apply path that relied on them, just before it sends). Same write as
    `apply_add`: temp file + os.replace, whole-file read-back, ValueError (nothing written) on any failure."""
    from datetime import date
    return _apply(file_path, story_id, lambda md: (stamp_used(md, ids, date.today().isoformat()), None))
