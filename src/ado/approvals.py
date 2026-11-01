import datetime
import hashlib
import os
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


# ---- comment approval inside the five applies (P4-12a) ------------------------------------------------------------------------------------

VERB_GATES = {"questions-post": {"approve-questions", "approve-plan-questions", "approve-answers-questions"}, "push": {"approve-push", "approve-sync"},
              "decisions-post": {"approve-decisions"}, "pr-create": {"approve-pr"}, "branch-link": {"branch-link"}}  # the ONLY applies a comment can authorise
_REQUEST = re.compile(r"\[spec-kit-ado:approval-request\][ \t]+([a-z][a-z0-9-]{2,40})[ \t]+([0-9a-f]{64})")  # the kit's own request comment (P4-13 posts it)
E_MISSING = ("comment approval: no valid approval for this plan on story {story}. A trusted approver must comment `/approve <gate> <hash>` (first line, "
             "unedited, newer than any approval request) with one of: {gates}.{bad}")
E_UNREADABLE = "comment approval: the story's comments are unreadable or incomplete (page limit, repeated page token or an unexpected answer), so nothing is approved."
E_NO_APPROVERS = "comment approval: no approvers are configured on the target branch (`approvers` in ado.config.json there), so nothing can be approved."
E_IDENTITY = "comment approval: the token identity cannot be established, so the comments cannot be attributed and nothing is approved."


class ApprovalMissing(ApprovalRefused):
    """The apply has no valid approver comment for this plan (or cannot tell): exit 2, nothing written. Fixed text, never ADO text."""


def gate_hash(gate_id, story_id, data):
    """What an approver's `/approve <gate> <hash>` names: sha256 over the gate id, the story id and the plan bytes (64 lowercase hex)."""
    return hashlib.sha256(b"spec-kit-ado/1\n" + gate_id.encode("utf-8") + b"\n" + str(story_id).encode("utf-8") + b"\n" + data).hexdigest()


def _requests(found, hashes):
    """Creation keys of every approval-request comment for any (gate, hash) of the set; None in the list = a time that cannot be read."""
    return [_order(c) for c in found if (m := _REQUEST.match(c.get("text") or "")) and hashes.get(m.group(1)) == m.group(2)]


def _approve_for(client, trust_cfg, story_id, make_hashes, message, ci_identity=None):
    """The core of `require` and of `check`: `make_hashes()` gives {gate: hash}, called after `comment_mode_allowed`. These gates are ONE target: the newest
    valid command over all of them (a command with any other hash still counts as a command, so a later reject or another plan's approval cancels) must be
    an `/approve` with that gate's hash, newer than every approval request for the set, with no newer malformed approver line (`newest()["bad_newer"]`) on
    any gate of the set. Anything else raises ApprovalMissing(`message(hashes, bad)`); the bypass refusal is ApprovalRefused. Reads only."""
    comment_mode_allowed(client)
    hashes = make_hashes()
    found, skip = _load(client, trust_cfg, story_id, ci_identity)

    def scan(rows):
        wins, bad = {}, 0
        for g in hashes:
            tally = {}
            wins[g] = newest(client, story_id, rows, g, trust_cfg.approvers, skip, tally=tally)
            bad = max(bad, tally.get("bad_newer", 0))
        return {g: w for g, w in wins.items() if w}, bad

    wins, bad = scan(found)
    key = lambda w: _order({"date": w["created"], "id": w["id"]})  # noqa: E731
    g, best = max(wins.items(), key=lambda kv: key(kv[1]), default=(None, None))
    if best and best["verb"] == "approve" and best["hash"] == hashes[g]:
        reqs, cut = _requests(found, hashes), key(best)
        if None not in reqs and all(r < cut for r in reqs):
            # a malformed approver line anywhere in the set newer than the winner: the comments newer than it are read again, so a line older than the approval is harmless
            _, bad = scan([c for c in found if (k := _order(c)) is None or k > cut])
            if not bad:
                return
    raise ApprovalMissing(message(hashes, bad))


def _load(client, trust_cfg, story_id, ci_identity=None):
    """(every comment of the story with its meta, the excluded identities) after the approvers are known to be set; ApprovalMissing with a fixed text when the
    comments are unreadable or truncated or the token identity is unknown. The caller has run `comment_mode_allowed`."""
    if not trust_cfg.approvers:
        raise ApprovalMissing(E_NO_APPROVERS)
    try:
        found, truncated = comments.fetch(client, story_id, with_meta=True)
    except comments.CommentsUnreadable:
        raise ApprovalMissing(E_UNREADABLE) from None
    if truncated:
        raise ApprovalMissing(E_UNREADABLE)
    try:
        return found, excluded(client, found, ci_identity)
    except ValueError:  # `questions._me` puts the client's error text into its message
        raise ApprovalMissing(E_IDENTITY) from None


def require(client, trust_cfg, story_id, verb, plan_bytes, ci_identity=None):
    """The gate in front of an ADO write in `approval: "comment"` mode (a no-op otherwise); called after the `--expect-plan` check. The verb's gates
    (`VERB_GATES[verb]`) are ONE target for this plan (see `_approve_for`; each gate's hash is `gate_hash(g, story_id, plan_bytes)`): fail closed, and
    the count of newer malformed lines is shown. A refusal has a fixed text (`ApprovalRefused`: bypass bit; `ApprovalMissing`: the rest). Reads only; an HTTP error propagates."""
    if trust_cfg.approval != "comment":
        return
    _approve_for(client, trust_cfg, story_id, lambda: {g: gate_hash(g, story_id, plan_bytes) for g in sorted(VERB_GATES[verb])},
                 lambda hashes, bad: E_MISSING.format(story=int(story_id), gates=", ".join(f"{g} {h}" for g, h in hashes.items()), bad=_bad(bad)),
                 ci_identity)


def _bad(n, what="for this plan"):
    return f" {n} newer malformed command line(s) by an approver {what}: check the thread." if n else ""


# ---- `ado approvals check` (P4-12b): the review gates by hash, the wait gates by ADO state --------------------------------------------------------

GATES = ("review-spec", "approve-questions", "approve-plan-questions", "wait-po-answers", "approve-answers-questions", "review-plan", "approve-push",
         "approve-decisions", "approve-sync", "review-review", "approve-pr", "wait-po-validation")  # the 12 gates of the shipped workflow, fixed
REVIEW_GATES = ("review-spec", "review-plan", "review-review")  # approved by hash over their artifacts
WAIT_GATES = ("wait-po-answers", "wait-po-validation")  # met by what ADO shows, no hash
E_GATE = "unknown gate: --gate must be one of the 12 gates of the ado-lifecycle workflow (docs/configuration.md, 'Approvers and comment mode')"
E_APPLY_GATE = "gate {gate} is not checked here: the apply it guards (`--apply`) requires the approver comment itself"
E_ARTIFACT = ("comment approval: an artifact this gate shows cannot be read (missing, a symlink, outside the working tree, over 1 MiB, not UTF-8, or not in the form the "
              "gate needs), so there is nothing to approve")
E_REVIEW = ("comment approval: no valid approval for gate {gate} on story {story}. A trusted approver must comment `/approve {gate} {hash}` (first line, unedited, "
            "newer than any approval request).{bad}")
E_NOT_WAIT = "gate {gate} is not a wait gate: it is checked by `ado approvals check` as a review or an apply gate, not by its open questions or decisions"
E_WAIT = "comment approval: gate {gate} on story {story} is not met: {n} open ({ids}).{bad}"
E_QUESTIONS = ("comment approval: the story's questions comment cannot be trusted (more than one comment carries the questions marker, it is not the token identity's own, "
               "it was edited by another identity, or it has unexpected markup or lines), so nothing is met.")
E_DECISIONS = ("comment approval: a decisions comment on the story cannot be trusted (edited, disguised, cut, hidden in markup, unexpected lines, or none of them is "
               "the token identity's own), so nothing is met.")
E_AGE_UNKNOWN = "comment approval: how long the questions have been shown cannot be established from the server (versions of the kit's comment), so nothing is met."
E_NO_TIME = "comment approval: the server time cannot be established, so no question can be due under `assume_after_hours`; nothing is met."
_REVIEW_H, _FIDELITY_H = re.compile(r"^### Review\b"), re.compile(r"^### AC fidelity\b")


def _block(sec, heading):
    """The authored text of the newest live (not `(superseded)`) block of ledger section `sec` that starts with `heading`, up to the next `#`..`###` heading;
    fences and comments hide headings. None when there is none or the section cannot be told apart from its visible form."""
    raw, vis = sec.splitlines(keepends=True), dod._visible(sec)[0].split("\n")
    if len(raw) != len(vis):
        return None
    live = [i for i, line in enumerate(vis) if heading.match(line) and not line.rstrip().endswith("(superseded)")]
    if not live:
        return None
    end = next((j for j in range(live[-1] + 1, len(vis)) if re.match(r"#{1,3} ", vis[j])), len(vis))
    return "".join(raw[live[-1]:end])


def artifacts(gate_id, story_id, cfg, repo="."):
    """The bytes a review gate shows its approver (D§4.2), for `gate_hash`: UTF-8 text of the listed artifacts, each framed as `<label> <byte count>\\n<bytes>\\n`
    (so no content can pass for a frame): `review-spec` = spec.md + ledger section 2; `review-plan` = plan.md + spec.md + ledger section 2; `review-review` =
    the newest live `### Review` block of ledger section 4, then (strict mode only) the newest live `### AC fidelity` block (an empty frame when there is none),
    then HEAD's full commit id. spec.md and plan.md sit in the ledger's `Spec dir`. Every file is read confined to `repo` (`gate._read`: no symlink,
    realpath inside, 1 MiB, UTF-8); the result depends on file contents and HEAD only, never on paths or time. ValueError (E_ARTIFACT) for anything missing or unreadable."""
    from . import gate, prs
    if gate_id not in REVIEW_GATES:
        raise ValueError(E_ARTIFACT)
    ledger = gate._read(os.path.join(repo, dod.dod_file(cfg.dod_path, comments._num(story_id))), repo, set())
    if ledger is None or dod.unclosed(ledger):
        raise ValueError(E_ARTIFACT)
    parts = []

    def spec_file(name):
        spec_dir = dod.ledger_spec_dir(ledger)
        text = gate._read(os.path.join(repo, spec_dir, name), repo, set()) if spec_dir else None
        if text is None:
            raise ValueError(E_ARTIFACT)
        parts.append((name, text))

    def ledger_part(label, text):
        if text is None:
            raise ValueError(E_ARTIFACT)
        parts.append((label, text))

    if gate_id == "review-plan":
        spec_file("plan.md")
    if gate_id != "review-review":
        spec_file("spec.md")
        ledger_part("ledger-2", dod.section(ledger, 2))
    else:
        sec4 = dod.section(ledger, 4)
        ledger_part("review", _block(sec4, _REVIEW_H) if sec4 is not None else None)
        if cfg.mode == "strict":
            parts.append(("fidelity", _block(sec4, _FIDELITY_H) or ""))
        try:
            head = prs._text(prs._git(repo, "rev-parse", "--verify", "HEAD^{commit}"), "HEAD").strip()
        except prs._Refuse:
            raise ValueError(E_ARTIFACT) from None
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise ValueError(E_ARTIFACT)
        parts.append(("head", head))
    return b"".join(f"{label} {len(b)}\n".encode() + b + b"\n" for label, b in ((label, text.encode("utf-8")) for label, text in parts))


def _wait_answers(client, story_id, trust_cfg, found, skip):
    """(open question ids, malformed-line count) of `wait-po-answers` from ADO only. There is at most ONE comment that carries the questions marker
    (`comments.marked`, so a disguised one counts), and it must be the token identity's own plain kit comment (anything else, a second marker comment, one by
    another identity, an edit by another identity, a text that is not exactly a shape the kit writes: E_QUESTIONS); no marker comment is nothing to wait for.
    Every question that was OPEN in ANY version of that comment (its versions) stays open until the newest valid approver `/answer Q<n>` (`newest`: the
    guards) that is newer than the version that started to show it, with no newer malformed approver line, or until it is due (`assume_after_hours` by
    `client.server_now()`, counted from that version); a question that vanished from the current text is not answered by vanishing."""
    from . import questions
    marked = [c for c in found if comments.marked(c.get("text"), questions.MARKER)]
    if not marked:
        return [], 0
    try:
        own = questions.own_comment(client, found)
    except ValueError:
        raise ApprovalMissing(E_QUESTIONS) from None
    if own is None or [c["id"] for c in marked] != [own["id"]]:
        raise ApprovalMissing(E_QUESTIONS)
    try:
        vs = questions.versions_of(client, story_id, own)
    except ValueError:
        raise ApprovalMissing(E_AGE_UNKNOWN) from None
    held = [questions.posted_open(v["text"]) for v in vs]
    if vs[-1]["text"] != own["html"] or None in held:
        raise ApprovalMissing(E_QUESTIONS)
    try:
        asked = questions.ages(client, story_id, held[-1], found, vs)
    except ValueError:
        raise ApprovalMissing(E_AGE_UNKNOWN) from None
    first = {}  # Q<n> -> index of the first version that showed it as open
    for i, h in enumerate(held):
        for q in h:
            first.setdefault(q["id"], i)
    now_ids = {q["id"] for q in held[-1]}
    hours = trust_cfg.unattended.get("assume_after_hours")
    hours = hours if type(hours) is int and 1 <= hours <= 720 else None
    now, still, bad_total = None, [], 0
    for i in sorted(first, key=lambda q: int(q[1:])):
        when = asked.get(i) if i in now_ids else _when(vs[first[i]]["modified"])
        if when is None and i not in now_ids:
            raise ApprovalMissing(E_AGE_UNKNOWN)
        tally = {}
        win = newest(client, story_id, found, i, trust_cfg.approvers, skip, tally=tally)
        key = _order({"date": win["created"], "id": win["id"]}) if win else None
        if win and win["verb"] == "answer" and not win["bad_newer"] and when and key and key > (when, (2, 0)):
            continue  # answered; a tie with the posting loses, as for `questions --sync`
        if hours and when:
            now = now or _server_now(client)
            if now - when >= datetime.timedelta(hours=hours):
                continue  # due
        still.append(i)
        bad_total += tally.get("bad_newer", 0)
    return still, bad_total


def _server_now(client):
    try:
        now = client.server_now()
    except AdoError:
        now = None
    if now is None:
        raise ApprovalMissing(E_NO_TIME)
    return now


def _wait_validation(client, story_id, trust_cfg, found, skip):
    """(open decision ids, malformed-line count) of `wait-po-validation` from ADO only. Every comment that carries the decisions marker (`comments.marked`, so
    a disguised one counts) must be a plain kit posting, as `ado done`'s cross-check reads it (E_DECISIONS otherwise): starts with the marker, has a readable
    time, was never edited, is not hidden in markup, shows the heading and only `D<n> (kind): ...` lines, none of them cut at the posting limit; at least one
    posting must be the token identity's. Every `D<n>` they list needs the newest valid `/approve|/reject D<n>` (`newest`) newer than the newest posting
    that shows it (or its last modification), with no newer malformed approver line. No posting at all is nothing to wait for."""
    from . import decisions, questions
    posts = {}  # D<n> -> the newest kit decisions comment that shows it
    marked = [c for c in found if comments.marked(c.get("text"), decisions.MARKER)]
    if not marked:
        return [], 0
    try:
        me = _guid(questions._me(client))
    except ValueError:
        raise ApprovalMissing(E_IDENTITY) from None
    if me is None or not any(c.get("author") == me for c in marked):
        raise ApprovalMissing(E_DECISIONS)
    edits, cut_at = {}, {}
    for c in marked:
        lines = comments.kit_lines(str(c.get("html") or ""), decisions.MARKER)
        if str(c["id"]) not in edits:
            edits[str(c["id"])] = _edited(client, story_id, c)
        ids = [(decisions.POSTED_LINE.match(ln), ln) for ln in lines if ln != decisions.MARKER and ln != decisions._HEAD]
        if (not str(c.get("text") or "").lstrip().startswith(decisions.MARKER) or _order(c) is None or edits[str(c["id"])] or decisions._HEAD not in lines
                or any(m is None for m, _ in ids)):
            raise ApprovalMissing(E_DECISIONS)
        for m, ln in ids:
            i = f"D{int(m.group(1))}"
            if i not in posts or _order(c) > _order(posts[i]):
                posts[i] = c
                cut_at[i] = len(ln) >= decisions.MAX_COMMENT_LINE and ln.endswith("…")  # the kit's own cut: the PO could not see the whole line
    still, bad_total = [], 0
    for i in sorted(posts, key=lambda d: int(d[1:])):
        post, tally = posts[i], {}
        win = newest(client, story_id, found, i, trust_cfg.approvers, skip, tally=tally)
        pkey = decisions._posting_key(post)
        key = _order({"date": win["created"], "id": win["id"]}) if win else None
        if cut_at[i] or not (win and win["verb"] in ("approve", "reject") and not win["bad_newer"] and pkey and key and key > pkey):
            still.append(i)  # a decision whose NEWEST posting is cut stays open: an approver cannot approve text they cannot see in full
            bad_total += tally.get("bad_newer", 0)
    return still, bad_total


def wait_state(client, story_id, gate_id, trust_cfg, ci_identity=None):
    """`{"met": bool, "open": [ids], "bad_newer": int}` for `wait-po-answers` (open question ids) or `wait-po-validation` (open decision ids), computed from
    ADO's comments ONLY, never from the ledger or the decisions file (an agent can edit those). `bad_newer` counts malformed approver lines newer than the
    answer or verdict on the open ids (the caller shows it). Runs `comment_mode_allowed` first; ApprovalRefused / ApprovalMissing with a fixed text for anything
    unreadable, truncated or unknown (never a pass). Reads only; an HTTP error propagates."""
    if gate_id not in WAIT_GATES:
        raise ApprovalRefused(E_NOT_WAIT.format(gate=gate_id) if gate_id in GATES else E_GATE)
    comment_mode_allowed(client)
    found, skip = _load(client, trust_cfg, story_id, ci_identity)
    still, bad = (_wait_answers if gate_id == "wait-po-answers" else _wait_validation)(client, story_id, trust_cfg, found, skip)
    return {"met": not still, "open": still, "bad_newer": bad}


def check(client, trust_cfg, story_id, gate_id, cfg, repo=".", ci_identity=None):
    """`ado approvals check`: a dict `{"gate", "mode", "met": True, ...}` when the gate is met, else ApprovalRefused / ApprovalMissing (fixed text; nothing from a
    comment). An unknown gate is refused first. Terminal mode (`approval != "comment"`) is a no-op that reads nothing from ADO. Comment mode runs
    `comment_mode_allowed` first; a review gate needs the newest valid approver command over the gate to be `/approve <gate> <hash of artifacts()>` (the
    shared `_approve_for`, so a request, a newer reject and newer malformed lines count as for the applies); a wait gate needs `wait_state` met; the seven
    apply gates are checked by their own `--apply` (`require`) and are refused here."""
    if gate_id not in GATES:
        raise ApprovalRefused(E_GATE)
    if trust_cfg.approval != "comment":
        return {"gate": gate_id, "mode": "terminal", "met": True}
    if gate_id in WAIT_GATES:
        st = wait_state(client, story_id, gate_id, trust_cfg, ci_identity)
        if not st["met"]:
            raise ApprovalMissing(E_WAIT.format(gate=gate_id, story=int(story_id), n=len(st["open"]), ids=", ".join(st["open"]), bad=_bad(st["bad_newer"], "on these")))
        return {"gate": gate_id, "mode": "comment", "met": True, "open": []}
    if gate_id not in REVIEW_GATES:
        raise ApprovalRefused(E_APPLY_GATE.format(gate=gate_id))
    _approve_for(client, trust_cfg, story_id, lambda: {gate_id: gate_hash(gate_id, story_id, artifacts(gate_id, story_id, cfg, repo))},
                 lambda hashes, bad: E_REVIEW.format(gate=gate_id, story=int(story_id), hash=hashes[gate_id], bad=_bad(bad)), ci_identity)
    return {"gate": gate_id, "mode": "comment", "met": True}
