import datetime
import html
import re
from pathlib import Path

from . import comments, workitems
from .client import AdoError, is_anonymous
from .dod import _esc, _text, _visible, section, section_span

UNPARSED_NOTE = "§2 has an unclosed code fence or comment; questions could not be read"
_OPEN = "_(open)_"
# `- [ ] Q1: <question> → <answer | _(open)_> · owner: <role>`; lenient: spacing, `->`, `*`/`+` bullet, no owner.
_LINE = re.compile(r"^\s*(?:[-*+]\s+)?\[([ xX])\]\s*Q(\d+)\s*:\s*(.*?)\s*$")
_EMPTY_ASSUMED = re.compile(r"assumed\s*:\s*$", re.I)
_OWNER = re.compile(r"\s*[·|]\s*owner\s*:\s*([^·|]*?)\s*$", re.I)
_ARROW = re.compile(r"\s*→\s*")
_ASCII_ARROW = re.compile(r"\s*->\s*")


def _parts(rest):
    """(question, answer, owner) of the text after `Q<n>:`; owner is `?` when the line names none."""
    owner = "?"
    if o := _OWNER.search(rest):
        rest, owner = rest[:o.start()], o.group(1) or "?"
    # the LAST `→` (a `->` in the question or the answer must not split it); only without one, the FIRST `->`
    arrows = list(_ARROW.finditer(rest)) or list(_ASCII_ARROW.finditer(rest))[:1]
    question, answer = (rest[:arrows[-1].start()], rest[arrows[-1].end():]) if arrows else (rest, "")
    return question.strip(), answer.strip(), owner


def parse_questions(ledger_md):
    """Ledger section 2 -> {'open': [...], 'answered': [...]}. Open: box `[ ]` or answer `_(open)_`/empty.
    Lines inside code fences or <!-- --> comments are ignored; `unparsed` is true when a fence or comment is
    left open in section 2 (later questions are then unreadable). No section 2 -> both empty."""
    out = {"open": [], "answered": []}
    body, out["unparsed"] = _visible(section(ledger_md, 2) or "")
    for line in body.splitlines():
        if not (m := _LINE.match(line)):
            continue
        question, answer, owner = _parts(m.group(3))
        # a bare `assumed:` (nothing after it) records no assumption, so the question stays open
        done = m.group(1) in "xX" and answer not in ("", _OPEN) and not _EMPTY_ASSUMED.match(answer)
        item = {"id": f"Q{m.group(2)}", "question": question, "owner": owner}
        out["answered" if done else "open"].append({**item, "answer": answer} if done else item)
    first = {}
    for q in out["open"]:
        first.setdefault(q["id"], q)  # duplicate ids are listed once, the first wins
    out["open"] = list(first.values())
    if out["unparsed"]:
        out["open"].append({"id": "?", "question": UNPARSED_NOTE, "owner": "?"})
    return out


# ADO strips HTML comments from comment text, so the marker is visible text (live-verified).
MARKER = "[spec-kit-ado:questions]"
_HEAD = "Open questions for the product owner:"


MAX_LINE = 500
ANSWERED = f"{MARKER}<br>All open questions are answered."


def _line(q):
    """(`Q<n>: <question>` capped at MAX_LINE chars with a trailing …, was it cut)."""
    text = f"{q['id']}: {q['question']}"
    return (text[:MAX_LINE - 1] + "…", True) if len(text) > MAX_LINE else (text, False)


_ASSUMED_HEAD = "The team assumed these; reply if one is wrong:"


def _assumed(answered):
    """Answered questions whose answer is an `assumed: …` (accepted by the operator without the product owner)."""
    return [q for q in answered if q.get("answer", "").lower().startswith("assumed")]


def comment_html(open_questions, assumed=()):
    """The comment ADO receives: marker, heading, one `Q<n>: <question>` line each (HTML-escaped, no owners or
    names), then the questions the team settled by assumption with that assumption. '' when there is neither.
    The unparsed-section note (id '?') is not a question and is left out."""
    qs = [q for q in open_questions if q["id"] != "?"]
    if not qs and not assumed:
        return ""
    parts = [MARKER] + ([_HEAD] + [html.escape(_line(q)[0]) for q in qs] if qs else [])
    if assumed:
        parts += [_ASSUMED_HEAD] + [html.escape(_line({**q, "question": f"{q['question']} → {q['answer']}"})[0])
                                    for q in assumed]
    return "<br>".join(parts)


def _me(client):
    """The authenticated identity id (org-level endpoint). Unresolvable -> ValueError: never guess whose comment it is.
    Without credentials connectionData answers 200 as the anonymous user, which is never "us"."""
    try:
        user = client.get("/_apis/connectionData?api-version=7.1-preview").get("authenticatedUser") or {}
    except AdoError as e:
        raise ValueError(f"cannot tell which ADO comments are yours ({e}); nothing posted")
    me = user.get("id")
    if not me:
        raise ValueError("cannot tell which ADO comments are yours (no authenticated user id); nothing posted")
    if is_anonymous(me, user.get("descriptor")):
        raise ValueError("cannot tell which ADO comments are yours (the request carried no valid credentials); "
                         "nothing posted")
    return me


def plan_post(client, work_item_id, parsed):
    """Read-only: {'action': none|add|edit|unchanged, 'html', 'comment_id', 'old', 'cut'}. Only a marked comment
    written by the authenticated identity is ever edited (the newest one); others are ignored. With every question
    answered, an own marked comment is edited to say so."""
    qs = [q for q in parsed["open"] if q["id"] != "?"]
    body = comment_html(qs, _assumed(parsed["answered"])) or ANSWERED
    cut = [q["id"] for q in qs if _line(q)[1]]
    me = _me(client)
    mine = [c for c in comments.fetch(client, work_item_id, with_id=True)
            if c["text"].startswith(MARKER) and c["author"] == me]
    if not mine:
        return {"action": "add" if qs else "none", "html": body if qs else "", "comment_id": None, "old": "",
                "cut": cut}
    last = mine[-1]
    action = "unchanged" if last["text"] == _text(body) else "edit"
    return {"action": action, "html": body, "comment_id": last["id"], "old": last["text"], "cut": cut}


def render_plan(work_item_id, plan, unparsed=False):
    verdict = {"none": "no open questions; nothing to post", "unchanged": "unchanged; nothing to post",
               "add": "ADD a new comment", "edit": f"EDIT the existing questions comment (id {plan['comment_id']})"}
    quote = lambda t: [f"> {_esc(x)}" for x in t.splitlines()]  # noqa: E731
    lines = [f"# Plan: post open questions to story {work_item_id}", "", f"Action: {verdict[plan['action']]}", ""]
    if unparsed:
        lines += ["Note: ledger section 2 has an unclosed fence or comment; later questions are not included.", ""]
    if plan["cut"]:
        lines += [f"Note: {', '.join(plan['cut'])} cut to {MAX_LINE} characters (…).", ""]
    if plan["action"] in ("add", "edit"):
        lines += ["Comment text:", ""] + quote(_text(plan["html"])) + [""]
    if plan["action"] == "edit":
        lines += ["Replaces:", ""] + quote(plan["old"]) + [""]
    return "\n".join(lines)


def apply_post(client, work_item_id, plan):
    """Post or edit exactly the planned comment; a no-op for none/unchanged."""
    base = f"/{workitems._proj(client)}/_apis/wit/workItems/{work_item_id}/comments"
    if plan["action"] == "add":
        client.post(f"{base}?api-version={comments.API}", {"text": plan["html"]})
    elif plan["action"] == "edit":
        client.patch(f"{base}/{plan['comment_id']}?api-version={comments.API}", {"text": plan["html"]})


# ---- `ado questions <id> --sync`: an approver's `/answer Q<n>: text` comment becomes the PO's answer (a local write); question age from SERVER time ----
SYNC_RULES = ("has-answer", "posting-lossy", "not-posted", "no-answer", "older-than-posting", "bad-comment-id")  # why an open question was left alone; fixed names
MAX_LEDGER_LINE = 2000  # a question line longer than this is malformed: `_LINE` backtracks quadratically over a long blank run
MAX_ANSWER = 500
MAX_VERSIONS, MAX_VERSION_TEXT = 500, 1 << 18
E_MALFORMED = ("the ledger's section 2 is malformed (a missing section, an unclosed fence or comment, a repeated or non-ASCII question id, or a question line "
               f"over {MAX_LEDGER_LINE} characters); nothing written")
E_AGE = "question age cannot be established from the server"
E_TIME = "the server time cannot be established, so no question can be due under `assume_after_hours`; nothing written"
_CAND = re.compile(r"\s*(?:[-*+]\s+)?\[[ xX]\]\s*Q\d")
_CID = re.compile(r"[1-9][0-9]{0,11}")  # a comment id: ASCII digits only, no leading zero
_ID = re.compile(r"Q[0-9]{1,9}")
_TAGS = re.compile(r"<(?!br\s*/?>)", re.I)  # anything but `<br>`: the kit's own comment holds nothing else (`html.escape` does the rest)
_SYNCED = re.compile(r"\(PO comment ([0-9]+)\)$")  # the provenance a sync writes at the end of an answer
_PO_COMMENT = re.compile(r"\bPO\s+comment\b", re.I)
_SEPARATORS = re.compile(r"->|[·|→]")


def check_ledger(md):
    """`parse_questions(md)` for a sync, or ValueError (E_MALFORMED): no section 2, an unclosed fence or comment, a repeated question id (open or answered:
    no first-wins), an id that is not `Q` + ASCII digits, or a question line over MAX_LEDGER_LINE (checked BEFORE the regexes run on it)."""
    span = section_span(md, 2)
    if span is None:
        raise ValueError(E_MALFORMED)
    cands = [v for v in _visible(md[span[0]:span[1]])[0].split("\n") if _CAND.match(v)]
    if any(len(v) > MAX_LEDGER_LINE for v in cands):
        raise ValueError(E_MALFORMED)
    ids = [f"Q{m.group(2)}" for v in cands if (m := _LINE.match(v))]
    parsed = parse_questions(md)
    if parsed["unparsed"] or len(ids) != len(set(ids)) or not all(_ID.fullmatch(i) for i in ids):
        raise ValueError(E_MALFORMED)
    return parsed


_BR = re.compile(r"<br\s*/?>", re.I)


def _kit_lines(raw):
    """The lines of one version of the kit's questions comment, or none: the lines are what lies between the kit's own `<br>`; any other markup (a line
    hidden in a tag, a style or a script, which the PO would not see) makes the version count for nothing, a version whose first line does not start with the
    questions marker is not the kit's comment, a huge version is not read, and a piece of text that breaks into two lines by itself (`&#10;`, a raw newline)
    is not a line (it would show as one line to the PO)."""
    if len(raw) > MAX_VERSION_TEXT or _TAGS.search(raw):
        return frozenset()
    parts = [_text(x).strip() for x in _BR.split(raw)]
    if not parts[0].startswith(MARKER):
        return frozenset()
    return frozenset(x for x in parts if "\n" not in x)


def ages(client, story_id, open_questions, rows):
    """{Q<n>: datetime (UTC)}: for each open question, the server time at which its CURRENT rendered line (`_line`, HTML-unescaped, the whole line) started
    to be shown in the kit's questions comment, read ONLY from that comment's versions endpoint (`comments.versions`, preview.1: each version's `modifiedDate`
    is the server's, when it was written). The comment is the NEWEST `[spec-kit-ado:questions]` comment of the token identity in `rows`
    (`comments.fetch(..., with_meta=True)`); the age is the `modified` of the first version of the unbroken run of versions, ending at the current
    one, that all hold the line: a line that was changed and changed back restarts at the change back, so the question is never older than the time the
    PO could have seen it; a no-op edit does not restart it. A question whose line is cut (> MAX_LINE) or not in the current version has no entry.
    ValueError (E_AGE + a fixed reason key; assume-and-flag refuses) when the token identity is unknown (`no-identity`), there is no such comment
    (`no-comment`), it was edited by another identity (`edited-by-other`), the versions endpoint is missing (404: `versions-unavailable`) or answers
    something that is not versions 1..n matching the comment (`versions-shape`), or a date is unreadable (`date-shape`). There is no fallback to a stored timestamp.
    Any other HTTP error propagates (AdoError). No request is made when nothing needs an age. `rows` and `open_questions` must come from the same
    ledger text the sync uses."""
    from . import approvals
    want = {}
    for q in open_questions:
        text, cut = _line(q) if q["id"] != "?" else ("", True)
        if not cut:
            want[q["id"]] = _text(html.escape(text)).strip()
    if not want:
        return {}
    fail = lambda why: ValueError(f"{E_AGE} ({why}); nothing written")  # noqa: E731
    try:
        me = approvals._guid(_me(client))
    except ValueError:  # `_me` puts the client's error text into its message
        raise fail("no-identity") from None
    if me is None:
        raise fail("no-identity")
    mine = [(approvals._order(c), c) for c in rows if c.get("author") == me and str(c.get("text") or "").startswith(MARKER)]
    if not mine:
        raise fail("no-comment")
    if any(k is None for k, _ in mine):
        raise fail("date-shape")
    comment = max(mine, key=lambda t: t[0])[1]
    if comment.get("modified_by") != comment.get("author"):
        raise fail("edited-by-other")
    try:
        vs = comments.versions(client, story_id, comment["id"])
    except AdoError as e:
        if e.status == 404:
            raise fail("versions-unavailable") from None
        raise
    except ValueError:
        raise fail("versions-shape") from None
    if not 1 <= len(vs) <= MAX_VERSIONS or [v["version"] for v in vs] != list(range(1, len(vs) + 1)) or comment.get("version") != len(vs):
        raise fail("versions-shape")
    held = [_kit_lines(v["text"]) for v in vs]
    out = {}
    for qid, line in want.items():
        i = len(vs)
        while i > 0 and line in held[i - 1]:
            i -= 1
        if i < len(vs):
            when = approvals._when(vs[i]["modified"])
            if when is None:
                raise fail("date-shape")
            out[qid] = when
    return out


def _answer_text(text):
    """An approver's answer as ledger text: the grammar's own separators (`→`, `->`, `·`, `|`) turned into `-`, control characters, `<!--`, `-->` and
    fences into blanks, whitespace collapsed, the words `PO comment` neutralised (only the sync writes `(PO comment <id>)`), an answer that would read as
    the team's assumption (`assumed...`) prefixed so it never does, cut to MAX_ANSWER. May be empty."""
    from . import decisions
    t = _SEPARATORS.sub("-", text)
    t = " ".join(decisions._MARKUP.sub(" ", decisions._CTRL.sub(" ", t)).split())
    t = _PO_COMMENT.sub("PO-comment", t)
    if t.lower().startswith("assumed"):
        t = "PO answer: " + t
    return t[:MAX_ANSWER].strip()


def sync(md, comment_rows, approvers, excluded, client, story_id, now, assume_after_hours, asked):
    """(the new ledger text, {"answered": [ids], "open": [ids still open], "due": [ids], "skipped": {id: rule}, "bad_newer": {id: n}, "ignored": [ids]}): ids and fixed
    rule names (SYNC_RULES) only, never question or answer text. `comment_rows` is `comments.fetch(..., with_meta=True)` output (the caller refused a
    truncated or unreadable read), `approvers`/`excluded` lowercase identity GUIDs, `asked` the `ages()` result for THIS ledger text.
    For every OPEN question (a question that already has an answer is never replaced: change it by hand in a reviewed commit) whose line has no answer text
    yet (`has-answer`), is not cut (`posting-lossy`) and is shown in the kit's questions comment (`not-posted`), the newest VALID `/answer Q<n>: text`
    (`approvals.newest`: the grammar and the guards; `no-answer`) is taken when it is NEWER than the version that started to show the line (`older-than-posting`:
    by created time, then id, a tie loses; the server's times, never the local clock) and its id is a plain comment id (`bad-comment-id`). The line is
    rewritten to `- [x] Q<n>: <question> → <answer> (PO comment <id>) · owner: PO`; every other byte is unchanged. `bad_newer[id]` counts malformed
    command lines by an approver newer than the answer (`newest`): the caller must show it. `due` lists the questions still open whose age is at least
    `assume_after_hours` (1..720) hours by `now`, the SERVER's clock (`client.server_now()`); with no `assume_after_hours` nothing is due; with it and no
    `now` ValueError. `ignored` lists the ANSWERED questions on which an approver's newest valid `/answer` is not the comment named by the line's `(PO comment <id>)` (or the line names none): never applied, only reported. Due questions are only listed: `plan --close-assumed` is the only thing that closes one. ValueError (nothing written) for a malformed ledger
    (`check_ledger`) or when an approver's valid answer cannot be written (inline comment on the line, a result over MAX_LEDGER_LINE or one that does not read back)."""
    from . import approvals
    parsed = check_ledger(md)
    hours = assume_after_hours
    if hours is not None:
        if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 720:
            raise ValueError("assume_after_hours must be a whole number of hours from 1 to 720; nothing written")
        if not isinstance(now, datetime.datetime) or now.tzinfo is None:
            raise ValueError(E_TIME)
    a, b = section_span(md, 2)
    raws = md[a:b].splitlines(keepends=True)
    lines = _visible(md[a:b])[0].split("\n")
    at = {}
    for n, v in enumerate(lines[:len(raws)]):
        if _CAND.match(v) and (m := _LINE.match(v)):
            at[f"Q{m.group(2)}"] = (n, m)
    written, skipped, bad_newer, blocked, still = {}, {}, {}, [], []
    for q in sorted(parsed["open"], key=lambda q: int(q["id"][1:])):
        i = q["id"]
        n, m = at[i]
        _, answer, _ = _parts(m.group(3))
        if answer not in ("", _OPEN) and not _EMPTY_ASSUMED.match(answer):
            skipped[i] = "has-answer"  # the box is open but the line holds text a person wrote: never overwritten
        elif _line(q)[1]:
            skipped[i] = "posting-lossy"  # the comment shows only the cut line: a changed tail would still match
        elif not isinstance(asked.get(i), datetime.datetime) or asked[i].tzinfo is None:
            skipped[i] = "not-posted"
        else:
            tally = {}
            win = approvals.newest(client, story_id, comment_rows, i, approvers, excluded, tally=tally)
            bad_newer[i] = tally.get("bad_newer", 0)
            key, cid = (approvals._order({"date": win["created"], "id": win["id"]}) if win else None), str(win["id"]) if win else ""
            if win is None or win["verb"] != "answer":
                skipped[i] = "no-answer"
            elif key is None or key <= (asked[i], (2, 0)):  # a tie loses, as for decisions
                skipped[i] = "older-than-posting"
            elif not _CID.fullmatch(cid):
                skipped[i] = "bad-comment-id"
            else:
                text = _answer_text(win["text"])
                v, raw = lines[n], raws[n]
                eol = raw[len(raw.splitlines()[0]):]  # splitlines also ends a line at U+2028, \x0c, \x85, \x1c: keep that terminator
                new = f"{v[:m.start(1)]}x{v[m.end(1):m.start(3)]}{q['question']} → {text} (PO comment {cid}) · owner: PO"
                if not text or v != raw.splitlines()[0] or len(new) > MAX_LEDGER_LINE:
                    blocked.append(i)
                else:
                    raws[n] = new + eol
                    written[i] = {"id": i, "question": q["question"], "owner": "PO", "answer": f"{text} (PO comment {cid})"}
                continue
        still.append(i)
    ignored = []
    for q in sorted(parsed["answered"], key=lambda q: int(q["id"][1:])):  # an answer is never replaced, but a newer valid /answer on it is reported, never dropped silently
        win = approvals.newest(client, story_id, comment_rows, q["id"], approvers, excluded)
        named = _SYNCED.search(q["answer"])
        if win is not None and win["verb"] == "answer" and (named is None or named.group(1) != str(win["id"])):
            ignored.append(q["id"])
    if blocked:
        raise ValueError(f"an approver's answer cannot be written to the ledger for {', '.join(blocked)} (an inline comment on the line, an empty answer or a line over "
                         f"{MAX_LEDGER_LINE} characters); fix the line and run it again; nothing written")
    out = md[:a] + "".join(raws) + md[b:]
    old_lines, new_lines = md.splitlines(keepends=True), out.splitlines(keepends=True)
    after = parse_questions(out)
    by_id = lambda ls: sorted(ls, key=lambda x: int(x["id"][1:]))  # noqa: E731
    if (len(old_lines) != len(new_lines) or sum(x != y for x, y in zip(old_lines, new_lines)) != len(written) or after["unparsed"]
            or after["open"] != [q for q in parsed["open"] if q["id"] not in written]
            or by_id(after["answered"]) != by_id(parsed["answered"] + list(written.values()))):
        raise ValueError("the sync would change more than the answered lines, or they do not read back; nothing written")
    due = []
    if hours is not None:
        limit = datetime.timedelta(hours=hours)
        due = [i for i in still if isinstance(asked.get(i), datetime.datetime) and asked[i].tzinfo is not None and now - asked[i] >= limit]
    return out, {"answered": list(written), "open": still, "due": due, "skipped": skipped, "bad_newer": bad_newer, "ignored": ignored}


def apply_sync(file_path, md, comment_rows, approvers, excluded, client, story_id, now, assume_after_hours, asked):
    """Run `sync` over `md` (the ledger text the caller read, and computed `asked` for) and write the result over the file in place (unique temp file +
    os.replace, only when something changed). Refuses (ValueError, nothing written) for a symlink, a file that is not a regular file or is over 1 MiB, a
    file that is no longer exactly `md`, or one that does not read back as written (the old text is then put back). Returns the `sync` report."""
    from . import decisions
    path = Path(file_path)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > decisions._MAX_FILE:
            raise ValueError("the ledger must be a regular file of at most 1 MiB, not a symlink; nothing written")
        new, report = sync(md, comment_rows, approvers, excluded, client, story_id, now, assume_after_hours, asked)
        if decisions._read(path) != md:
            raise ValueError("the ledger changed while syncing; nothing written, run it again")
        if new != md:
            decisions._atomic_write(path, new)
            if decisions._read(path) != new:
                decisions._atomic_write(path, md)
                raise ValueError("the ledger did not read back as written; nothing recorded")
    except (OSError, UnicodeError) as e:
        raise ValueError(f"cannot update the ledger ({e.__class__.__name__}); nothing written")
    return report
