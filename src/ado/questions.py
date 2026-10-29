import html
import re

from . import comments, workitems
from .client import AdoError
from .dod import _esc, _text, _visible, section

UNPARSED_NOTE = "§2 has an unclosed code fence or comment; questions could not be read"
_OPEN = "_(open)_"
# `- [ ] Q1: <question> → <answer | _(open)_> · owner: <role>`; lenient: spacing, `->`, `*`/`+` bullet, no owner.
_LINE = re.compile(r"^\s*(?:[-*+]\s+)?\[([ xX])\]\s*Q(\d+)\s*:\s*(.*?)\s*$")
_EMPTY_ASSUMED = re.compile(r"assumed\s*:\s*$", re.I)
_OWNER = re.compile(r"\s*[·|]\s*owner\s*:\s*([^·|]*?)\s*$", re.I)
_ARROW = re.compile(r"\s*→\s*")
_ASCII_ARROW = re.compile(r"\s*->\s*")


def parse_questions(ledger_md):
    """Ledger section 2 -> {'open': [...], 'answered': [...]}. Open: box `[ ]` or answer `_(open)_`/empty.
    Lines inside code fences or <!-- --> comments are ignored; `unparsed` is true when a fence or comment is
    left open in section 2 (later questions are then unreadable). No section 2 -> both empty."""
    out = {"open": [], "answered": []}
    body, out["unparsed"] = _visible(section(ledger_md, 2) or "")
    for line in body.splitlines():
        if not (m := _LINE.match(line)):
            continue
        rest = m.group(3)
        owner = "?"
        if o := _OWNER.search(rest):
            rest, owner = rest[:o.start()], o.group(1) or "?"
        # the LAST `→` (a `->` in the question or the answer must not split it); only without one, the FIRST `->`
        arrows = list(_ARROW.finditer(rest)) or list(_ASCII_ARROW.finditer(rest))[:1]
        question, answer = (rest[:arrows[-1].start()], rest[arrows[-1].end():]) if arrows else (rest, "")
        answer = answer.strip()
        # a bare `assumed:` (nothing after it) records no assumption, so the question stays open
        done = m.group(1) in "xX" and answer not in ("", _OPEN) and not _EMPTY_ASSUMED.match(answer)
        item = {"id": f"Q{m.group(2)}", "question": question.strip(), "owner": owner}
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
    """The authenticated identity id (org-level endpoint). Unresolvable -> ValueError: never guess whose comment it is."""
    try:
        me = (client.get("/_apis/connectionData?api-version=7.1-preview").get("authenticatedUser") or {}).get("id")
    except AdoError as e:
        raise ValueError(f"cannot tell which ADO comments are yours ({e}); nothing posted")
    if not me:
        raise ValueError("cannot tell which ADO comments are yours (no authenticated user id); nothing posted")
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
