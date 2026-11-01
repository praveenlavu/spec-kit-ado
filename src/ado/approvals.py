import datetime
import re
import unicodedata
import urllib.parse

from . import comments, dod, questions, workitems
from .client import AdoError, is_anonymous
from .stack import _clean

KIT_MARKER = "[spec-kit-ado:"  # every comment the kit posts starts with one (questions, decisions, ...)
MAX_HTML = 16384  # a command comment is tiny; more than this (after quotes are cut) is not parsed: `dod._text` is quadratic on long whitespace runs
NOTE_CAP, ANSWER_CAP = 300, 500
MAX_REVISION_PAGES = 50
PERM_NAMESPACE, BYPASS_BIT, PERM_API = "52d39943-cb85-4d7f-8fa8-c6baac873819", 1048576, "7.1"  # R:17
BYPASS_REFUSAL = ("comment approval is refused: this token identity holds the 'Bypass rules on work item updates' permission, or that could not be "
                  "checked, so a forged comment could not be told apart from a real one. Run the agent as a Contributor without that permission "
                  "(docs/configuration.md, 'Approvers and comment mode').")


class ApprovalRefused(ValueError):
    """A refusal with a fixed text (never ADO text): the verbs map it to exit 2."""


def excluded(client, comments, ci_identity=None):
    """Identity ids (lowercase) that can never count as an approver: the token identity (`questions._me`; unresolvable or anonymous raises
    ValueError, never a guess), the author of any comment that starts with a `[spec-kit-ado:` marker (the kit's posting identities) and, when
    given, `ci_identity` (the CI token identity; a value that is not a non-anonymous identity GUID raises ValueError: never skipped silently).
    `comments` is `comments.fetch(..., with_meta=True)` output (its authors are already lowercase GUIDs, or None and then skipped; `with_id` rows work too and are lowercased here). The anonymous identity is never added.
    shortcut: the local verbs do not know the CI identity; P4-13 passes it."""
    me = _guid(questions._me(client))
    if me is None:
        raise ValueError("cannot tell which ADO comments are yours (the token identity is not an identity id); nothing counted")
    out = {me}
    if ci_identity is not None:
        ci = _guid(ci_identity)
        if ci is None or is_anonymous(ci):
            raise ValueError("the CI token identity is not a valid identity id")
        out.add(ci)
    for c in comments:
        author = c.get("author")
        if str(c.get("text") or "").startswith(KIT_MARKER) and isinstance(author, str) and author.strip() and not is_anonymous(author):
            out.add(author.strip().lower())
    return out


def _guid(v):
    """`v` as a lowercase identity GUID; padding, braces and anything else is None (not repaired)."""
    return comments._guid(v)


# ---- bypass-rules refusal ------------------------------------------------------------------------------------------------------------------

def bypass_bit(client):
    """Does the token identity hold "Bypass rules on work item updates" on the project (R:17: permissions namespace 52d39943-…, bit 1048576, token
    `$PROJECT:vstfs:///Classification/TeamProject/<project id>`)? True / False, or None whenever it cannot be told: a request error (a restricted
    PAT may not read it: unknown), a project id that is no GUID, any other answer shape (anything but exactly one boolean). Reads only."""
    try:
        proj = client.get(f"/_apis/projects/{workitems._proj(client)}?api-version={PERM_API}")
        pid = proj.get("id") if isinstance(proj, dict) else None
        if not (isinstance(pid, str) and comments.GUID_RE.fullmatch(pid)):
            return None
        token = urllib.parse.quote(f"$PROJECT:vstfs:///Classification/TeamProject/{pid.lower()}", safe="")
        page = client.get(f"/_apis/permissions/{PERM_NAMESPACE}/{BYPASS_BIT}?tokens={token}&api-version={PERM_API}")
    except AdoError:
        return None
    value = page.get("value") if isinstance(page, dict) else None
    return value[0] if isinstance(value, list) and len(value) == 1 and type(value[0]) is bool else None


def comment_mode_allowed(client):
    """None when the token identity provably lacks the bypass bit; otherwise ApprovalRefused (fixed text) when the bit is True OR unreadable. This
    is the only protection against a forged author that is checked before any comment counts (a bypassRules write can set `System.ChangedBy`, and
    only `on_behalf` gives such a comment away)."""
    if bypass_bit(client) is not False:
        raise ApprovalRefused(BYPASS_REFUSAL)


# ---- quotes and the command grammar -------------------------------------------------------------------------------------------------------------

# matched AT a `<`; `dod._text` also reads `<` up to the first `>` as one tag. Groups: 1 blanks after `<`, 2 the slash of a closing tag, 3 the blanks after
# it, 4 the name. A browser reads `< /x>` as text and `</ x>` as a bogus comment, so a closing tag with a blank in 1 or 3 closes nothing. No two adjacent
# `\s*` (that backtracks quadratically over a long blank run).
_NAME = re.compile(r"<(\s*)(?:(/)(\s*))?([A-Za-z][A-Za-z0-9]*)")
_EQ = re.compile(r"\s*=\s*")
_VALUE = re.compile(r"[^\s>]*")
_QUOTE_TAGS = frozenset(("blockquote", "q"))
# elements whose content a browser reads as raw text (a `</blockquote>` inside one closes nothing); inside a quote they make it run to the end
_RAW_TEXT = frozenset(("script", "style", "textarea", "title", "xmp", "plaintext", "noscript", "iframe", "noembed", "noframes", "template", "svg", "math", "object"))


def _class_has_quote(tag):
    """Does the first `class=` of this opening tag hold the word `quote` (gmail_quote, yahoo_quoted, quote, ...)? Looks at the first 2000 characters only."""
    head = tag[:2000].lower()
    if "quote" not in head:
        return False
    k = 0
    while True:
        k = head.find("class", k)
        if k < 0:
            return False
        k += 5
        m = _EQ.match(head, k)
        if m:
            p = m.end()
            if p < len(head) and head[p] in "\"'":
                end = head.find(head[p], p + 1)
                return "quote" in head[p + 1:end if end >= 0 else len(head)]
            return "quote" in _VALUE.match(head, p).group()


def _skip(html, name, pos):
    """Index just after the `</name>` that closes an element opened right before `pos` (same-name elements nest); the end when it never closes."""
    depth, n = 1, len(html)
    while True:
        j = html.find("<", pos)
        gt = html.find(">", j) if j >= 0 else -1
        if gt < 0:
            return n
        if html[j + 1:j + 2] in ("!", "?"):  # a comment, doctype, CDATA or processing instruction: where it ends differs between parsers (`<!-- >`)
            return n
        m = _NAME.match(html, j)
        if m and not m.group(2) and m.group(4).lower() in _RAW_TEXT:
            return n
        if m and m.group(2) and (m.group(1) or m.group(3)):  # `< /name>`, `</ name>`: no close tag for a browser, so the end of the quote is unknown: quoted to the end
            return n
        if m and m.group(4).lower() == name:
            if html[m.end()] not in " \t\n\r\f/>" or (not m.group(2) and html[gt - 1] == "/"):
                return n  # `</name-x>` is another element for a browser, and a browser nests `<name/>`: the end of the quote is unknown
            if m.group(2):
                depth -= 1
                if depth == 0:
                    return gt + 1
            else:
                depth += 1
        pos = gt + 1


def strip_quotes(html):
    """`html` without quoted text, in ONE linear pass over its tags (a tag is `<` up to the first `>`, as `dod._text` reads it): every `<blockquote>`
    and `<q>` element and every element whose `class` contains `quote` (the reply markup of mail clients: gmail_quote, yahoo_quoted) goes, nested
    elements of the same name included; an element that never closes, or that holds a raw-text element (script, textarea, ...: a browser would not read a
    `</blockquote>` inside it as a close), is quoted to the end. A removed element leaves a `<br>`, so text before and after
    it are different lines. Over-stripping can only make a comment count LESS. Not a sanitizer: the result still goes through `dod._text`."""
    if not isinstance(html, str):
        return ""
    out, i = [], 0
    while True:
        j = html.find("<", i)
        gt = html.find(">", j) if j >= 0 else -1
        if gt < 0:
            break
        m = _NAME.match(html, j)
        if m and not m.group(2) and (m.group(4).lower() in _QUOTE_TAGS or _class_has_quote(html[j:gt + 1])):
            out += [html[i:j], "<br>"]
            i = _skip(html, m.group(4).lower(), gt + 1)
        else:
            out.append(html[i:gt + 1])
            i = gt + 1
    out.append(html[i:])
    return "".join(out)


_WS = re.compile(r"\s+")
_CMD = re.compile(r"/(approve|reject|answer)[ \t]+(D[1-9][0-9]{0,8}|Q[1-9][0-9]{0,8}|[a-z][a-z0-9-]{2,40})(?:[ \t]+([0-9a-f]{64}))?(?:[ \t]*:[ \t]*(.+))?")
_DQ = re.compile(r"[DQ][0-9]+")


def _one(m):
    """A whitespace run as one character: a newline if it holds one, else its first character that is not an ASCII blank, else a space."""
    run = m.group()
    return "\n" if "\n" in run else next((c for c in run if c not in " \t"), " ")


def _tame(s):
    """Make `dod._text` linear: its `<[^>]+>` rescans to the end from every `<` that has no `>` after it, and its `[ \\t]+\\n` from every character
    of a long run of blanks. A `<` after the last `>` becomes `&lt;` (read back as `<`), and any run of Unicode whitespace becomes one character
    (a newline when it holds one: line structure is kept; a no-break space or other odd blank stays, so it still breaks the grammar)."""
    s = s.replace("\r", "")
    last = s.rfind(">") + 1
    return _WS.sub(_one, s[:last] + s[last:].replace("<", "&lt;"))


def _note(raw):
    t = comments.scrub_emails(raw)
    t = comments._AT.sub("@user", t)
    return " ".join(_clean(t, cap=len(t)).split())


def _looks_like_command(line):
    """Does the line start like a command, however disguised: any case, full-width forms, invisible characters before it, `/approved`, `/approve-all`?"""
    head = "".join(c for c in line[:40] if unicodedata.category(c) != "Cf")
    return unicodedata.normalize("NFKC", head).lstrip().casefold().startswith(("/approve", "/reject", "/answer"))


def _parse(html):
    """(command or None, bad): `bad` is None, or for a first line that looks like a command (or a comment too long to read) but breaks the grammar,
    the word it names as its target, as typed ("" when there is none): the caller decides whether that line is about its target."""
    if not isinstance(html, str):
        return None, None
    s = strip_quotes(html)
    if len(s) > MAX_HTML:
        return None, ""
    text = dod._text(_tame(comments.scrub_mentions(s)))
    for line in text.split("\n"):
        line = line.strip(" \t")
        if line:
            break
    else:
        return None, None
    if all(unicodedata.category(c) in ("Cf", "Zs", "Zl", "Zp", "Cc") for c in line):
        return None, ""  # a first line that only LOOKS empty (zero-width characters): fail closed, but counted
    if not _looks_like_command(line):
        return None, None
    words = line.split(None, 2)
    named = words[1].split(":")[0] if len(words) > 1 else ""
    m = _CMD.fullmatch(line)
    if not m:
        return None, named
    verb, target, hash_, note = m.groups()
    if verb == "answer":
        ok = target[0] == "Q" and hash_ is None and note is not None
    else:
        ok = (target[0] == "D" and hash_ is None) if _DQ.fullmatch(target) else hash_ is not None
    if not ok:
        return None, named
    text = "" if note is None else _note(note)[:ANSWER_CAP if verb == "answer" else NOTE_CAP]  # capped, never refused: a long reject must still be a reject
    if note is not None and not text:
        return None, named
    return {"verb": verb, "target": target, "hash": hash_, "text": text}, None


def parse_command(html):
    """The command in a comment's raw HTML, or None: `{"verb", "target", "hash", "text"}`. Quoted text goes first (`strip_quotes`), then ONLY the
    first non-empty line counts, and it must be the whole command:
    `/approve|/reject D<n>[: note]`, `/answer Q<n>: text`, `/approve|/reject <gate-id> <64 lowercase hex>[: note]` (a gate needs the hash, a D or Q target
    must not carry one). A note is cut to 300 characters, an answer to 500 (capped, not refused: a long `/reject` must stay a reject); the text is scrubbed (addresses and mentions,
    control, invisible and markup characters). No error is ever raised for hostile input."""
    return _parse(html)[0]


# ---- guards ----------------------------------------------------------------------------------------------------------------------------------

def _when(s):
    """An ISO-8601 UTC timestamp (`...T..:..:..[.f]Z`, 1-7 fraction digits) as a datetime, else None. Compared as times: 'ss.5Z' is later than 'ss.25Z'."""
    m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,7}))?Z", s) if isinstance(s, str) else None
    if not m:
        return None
    try:
        y, mo, d, h, mi, sec = (int(x) for x in m.groups()[:6])
        return datetime.datetime(y, mo, d, h, mi, sec, int((m.group(7) or "0").ljust(6, "0")[:6]), tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def authorized_as(client, story_id, c):
    """Defence in depth, not the detector that works: the identity (lowercase GUID) that AUTHENTICATED the write of comment `c`, from the work item
    revision whose `System.ChangedDate` equals the comment's creation time (`$expand=all` is needed: R:79, confirmed live), or None when that
    cannot be told (no unique matching revision, odd shapes, the 50-page cap). A bypassRules forge cannot change this field, but the comment's own
    `createdBy` already stays the real actor and the forge shows as `createdOnBehalfOf`: that (`on_behalf`) is what catches it. Whether a comment's
    creation time always equals its revision's `System.ChangedDate` is NOT verified live: a None means "cannot tell", never "genuine". An HTTP
    error is raised (the caller must not treat it as a pass)."""
    when = _when(c.get("date"))
    if when is None:
        return None
    base = f"/{workitems._proj(client)}/_apis/wit/workItems/{comments._num(story_id)}/revisions?$expand=all&$top=200&api-version={client.config.api_version}"
    hits = []
    for page_no in range(MAX_REVISION_PAGES):
        page = client.get(f"{base}&$skip={page_no * 200}")
        items = page.get("value") if isinstance(page, dict) else None
        if not isinstance(items, list):
            return None
        for r in items:
            fields = r.get("fields") if isinstance(r, dict) else None
            if not isinstance(fields, dict):
                return None
            if _when(fields.get("System.ChangedDate")) == when:
                who = fields.get("System.AuthorizedAs")
                hits.append(_guid(who.get("id")) if isinstance(who, dict) else None)
        if len(items) < 200:
            return hits[0] if len(hits) == 1 else None
    return None


def _edited(client, story_id, c):
    """Unedited, or edited by its author with text identical in every version (a no-op save)? Anything unreadable counts as edited."""
    if c.get("modified_by") != c.get("author") or type(c.get("version")) is not int or c["version"] < 1:
        return True
    if c["version"] == 1:
        return False
    vs = comments.versions(client, story_id, c.get("id"))
    return not ([v["version"] for v in vs] == list(range(1, c["version"] + 1)) and all(v["text"] == c.get("html") for v in vs))


def guard(client, story_id, c, approvers, excluded):
    """Why comment `c` (a `comments.fetch(with_meta=True)` row) cannot count as an approver's, or None when it can:
    `on-behalf` (the raw comment had a `createdOnBehalfOf` key: what a bypassRules forge leaves), `excluded-author` (the token identity, a kit posting
    identity, the CI identity), `not-approver` (author not in `approvers`; compared as lowercase GUIDs), `edited` (version != 1 unless edited by the
    author with identical text in every version, the versions listed being exactly 1..n; an unreadable versions list is an edit), `forged` (`authorized_as` says another identity
    authenticated the write; skipped when that cannot be told). Cheap checks run first; the requests only for a comment that passed them."""
    author = c.get("author")
    author = author.lower() if isinstance(author, str) else None
    if c.get("on_behalf"):
        return "on-behalf"
    if author in {str(e).lower() for e in excluded}:
        return "excluded-author"
    if author is None or is_anonymous(author) or author not in {str(a).lower() for a in approvers}:
        return "not-approver"
    if _edited(client, story_id, c):
        return "edited"
    who = authorized_as(client, story_id, c)
    return "forged" if who is not None and who != author else None


_WELL_FORMED = re.compile(r"[DQ][1-9][0-9]*|[a-z][a-z0-9-]{2,40}")


def _elsewhere(word, target):
    """Is the target word of a malformed line (up to a colon) a DIFFERENT well-formed target? Anything else (punctuation: `D3.`, `(D3)`, `D3,`; `D03`;
    full-width digits; a typo; nothing) counts as possibly about `target`: a retraction typed wrong must be reported, never assumed to be elsewhere."""
    w = word.split(":")[0]
    return w != target and _WELL_FORMED.fullmatch(w) is not None


def _order(c):
    """Sort key (created time, id) of a comment row, or None when its time cannot be read."""
    when, cid = _when(c.get("date")), c.get("id")
    return None if when is None else (when, (0, int(cid)) if str(cid).isdigit() else (1, str(cid)))


def newest(client, story_id, comments, target, approvers, excluded, tally=None):
    """The newest VALID command for `target`, by (created time, id), as `{"verb", "target", "hash", "text", "id", "created", "author", "bad_newer"}`,
    else None. Valid = the grammar holds (`parse_command`) and `guard` returns None. An invalid comment is skipped, so a later invalid comment (the
    agent's, an edited or quoted one) never cancels an earlier valid one, while a later VALID `/reject` is the newest and does. Its `hash` is for
    the caller to compare with the gate's current hash.
    A MALFORMED command line by an approver (author in `approvers`, not excluded, not on behalf) that is newer than the winner and does not name a
    different well-formed target (`_elsewhere`: `/reject D3.` and `/reject (D3)` count for D3) cannot be told from a retraction typed wrong, so it is counted in `bad_newer` (and in `tally`, also when there is no
    winner): the caller must show that count (fixed text, a number only) next to the verdict, never ignore it.
    `tally` (a dict) is filled with counts only: `bad` (command-looking lines that break the grammar, any author and target), `bad_newer`, `date`
    (an unreadable creation time) and one count per guard rule; no comment text is ever kept or printed. An HTTP error from a guard is raised."""
    tally = {} if tally is None else tally
    count = lambda k, n=1: tally.__setitem__(k, tally.get(k, 0) + n)  # noqa: E731
    wanted, found, bads = {str(a).lower() for a in approvers}, [], []
    skip = {str(e).lower() for e in excluded}
    for c in comments:
        cmd, bad = _parse(c.get("html")) if isinstance(c, dict) else (None, None)
        if bad is not None:
            count("bad")
            author = c["author"].lower() if isinstance(c.get("author"), str) else None
            if author in wanted and author not in skip and not c.get("on_behalf") and not _elsewhere(bad, target):
                bads.append(_order(c))
        if cmd is None or cmd["target"] != target:
            continue
        key = _order(c)
        if key is None:
            count("date")
            continue
        found.append((key, cmd, c))
    out = None
    for key, cmd, c in sorted(found, key=lambda t: t[0], reverse=True):
        why = guard(client, story_id, c, approvers, excluded)
        if why is None:
            out = (key, {**cmd, "id": c["id"], "created": c["date"], "author": c["author"]})
            break
        count(why)
    newer = sum(1 for k in bads if k is None or out is None or k > out[0])
    if newer:
        count("bad_newer", newer)
    return None if out is None else {**out[1], "bad_newer": newer}
