"""Business rules written by a product owner in an Epic/Feature Description (V3 piece 3, P3-3): normalise the rich text to plain
lines, then parse `Rule <n>:` blocks. The input is UNTRUSTED text (pasted from Word, edited by anyone with edit rights): no rule is
dropped silently (anything doubtful becomes a named problem), text never becomes structure, and problems carry fixed keys only,
never rule text. Every pass is linear: input is capped, scans use str.find, and the reused `comments._MENTION` / `comments._EMAIL` and
`dod._text` regexes only ever see input shaped so they cannot backtrack badly (see `normalise`)."""
import re
import unicodedata

from . import comments, decisions, dod

BULLETS = "•·▪◦○●■□►▸➢➤✓✔§-*–—"  # fixed set; `o` only when alone, list numbers `1.`/`1)`; nothing else is stripped (`€5 off` stays)
MAX_RAW = 1 << 20  # ADO's Description limit; a bigger input is refused, not truncated
MAX_SECTION = 64 * 1024
MAX_RULES = 100
MAX_RULE = 2000

ISSUES = {  # key -> fixed text; level is carried by each problem
    "no-number": "a `Rule:` line has no number",
    "bad-number": "a rule number is not a whole number from 1 to 9999",
    "bad-rule-line": "a line looks like a rule but is not written `Rule <n>:`",
    "no-section-heading": "rules appear without a `Business rules` heading above them",
    "duplicate-number": "two rules share a number, so neither can be cited",
    "unknown-status": "the Status is not approved, draft or retired",
    "bad-status-line": "a Status line is not written `Status: <value>`",
    "two-status": "a rule has more than one Status line",
    "rule-too-long": "a rule is longer than 2000 characters",
    "section-too-big": "the Business rules section is over 64 KiB or 100 rules, or the Description is over 1 MiB",
    "no-parsable-rules": "the Business rules section holds no `Rule <n>:` line",
    "unclosed-markup": "an unclosed comment, style, script or xml block hides the text after it",
    "no-status": "no Status line (treated as draft)",
    "no-why": "no Why line",
    "no-example": "no Example line",
    "no-owner": "no Owner line",
}
_WARNINGS = frozenset(("no-status", "no-why", "no-example", "no-owner"))


def _problem(rule, issue):
    return {"rule": rule, "level": "warning" if issue in _WARNINGS else "error", "issue": issue}


# ---- normalise ----

_ELEMENTS = {n: re.compile(r"</" + n + r"(?![A-Za-z0-9:_-])", re.I) for n in ("style", "script", "xml")}
_ELEMENT_OPEN = re.compile(r"<(style|script|xml)(?![A-Za-z0-9:_-])", re.I)
_STRAY_LT = re.compile(r"<(?![A-Za-z/!?][^<>]*>)")  # a `<` that opens no complete tag is text (browsers agree); ADO escapes real ones
_A_CLOSE = re.compile(r"</a>", re.I)
# numeric/named entities that unescape to a space or tab: made non-breaking so `_text`'s `[ \t]+\n` cannot go quadratic on a run of them
_SPACE_ENTITY = re.compile(r"&#0*(?:32|9)(?![0-9]);?|&#[xX]0*(?:20|9)(?![0-9A-Fa-f]);?|&Tab;")
_ZS = re.compile("[   -   　]")
# Cf format characters (zero-width, soft hyphen, bidi marks): a superset of U+200B, U+2060, U+FEFF so `Ru​le 1:` cannot hide a rule
_INVISIBLE = re.compile("[­؜᠎​-‏‪-‮⁠-⁤⁦-⁯﻿]")
_CONTROL = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f]")
_LEAD = re.compile("(?:\\s|[" + re.escape(BULLETS) + "](?![0-9])|o(?=\\s|$))+")  # a sign glued to a digit stays: `-10 points`
_LIST_NUMBER = re.compile(r"[0-9]{1,3}[.)](?:\s+|$)")


def _drop_comments(s):
    """Every `<!--...-->` removed; returns (text, unclosed). An unclosed one drops the rest. A Word downlevel-revealed pair
    `<!--[if x]-->text<!--[endif]-->` is two complete comments, so the text between stays."""
    out, i = [], 0
    while True:
        a = s.find("<!--", i)
        if a < 0:
            out.append(s[i:])
            return "".join(out), False
        out.append(s[i:a])
        b = s.find("-->", a + 2)  # a + 2: `<!-->` and `<!--->` are complete (empty) comments, as in HTML
        if b < 0:
            return "".join(out), True
        i = b + 3


def _drop_elements(s):
    """`<style>`, `<script>`, `<xml>` elements removed (any case); returns (text, unclosed)."""
    out, i = [], 0
    while True:
        m = _ELEMENT_OPEN.search(s, i)
        if not m:
            out.append(s[i:])
            return "".join(out), False
        out.append(s[i:m.start()])
        close = _ELEMENTS[m.group(1).lower()].search(s, m.end())
        end = s.find(">", close.end()) if close else -1
        if end < 0:
            return "".join(out), True
        i = end + 1


def _mentions(s):
    """`comments._MENTION` only over text that ends in `</a>` and holds only complete tags, so its lazy body always finds its end."""
    if "data-vss-mention" not in s.lower():
        return s
    last = None
    for last in _A_CLOSE.finditer(s):
        pass
    if last is None:
        return s
    return comments._MENTION.sub("@user", s[:last.end()]) + s[last.end():]


_LOCAL = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+'-")  # the local-part class of comments._EMAIL


def _emails(s):
    """`comments._EMAIL` matched once per `@`, anchored where the local part starts (at most 64 characters back) and ending at most
    255 after: each character is looked at a bounded number of times, whatever the input. shortcut: an address longer than the RFC
    limits is cut there and its first characters stay; widen if that matters."""
    out, i = [], 0
    for at in re.finditer("@", s):
        p = at.start()
        if p < i or p == 0 or s[p - 1] not in _LOCAL or s.find(".", p, p + 256) < 0:
            continue  # cheap rejects: no address character before, no dot in the host window
        lo = p
        while lo > i and p - lo < 64 and s[lo - 1] in _LOCAL:
            lo -= 1
        m = comments._EMAIL.match(s, lo, min(len(s), p + 256))
        if m and m.end() > p:
            out.append(s[i:lo])
            out.append("[email]")
            i = m.end()
    out.append(s[i:])
    return "".join(out)


def normalise(html):
    """Rich text -> (lines, problems). Order: comments, style/script/xml elements, mentions, emails, `dod._text`, line breaks, space
    separators, invisible characters, then per line one leading run of BULLETS/whitespace and one list number, whitespace collapsed,
    empty lines dropped. Problems are `unclosed-markup` (text after it is dropped) or `section-too-big` (over 1 MiB: nothing is read)."""
    if len(html) > MAX_RAW:
        return [], [_problem(None, "section-too-big")]
    problems = []
    s, bad1 = _drop_comments(html)
    s, bad2 = _drop_elements(s)
    if bad1 or bad2:
        problems.append(_problem(None, "unclosed-markup"))
    s = _STRAY_LT.sub("&lt;", s)
    s = _emails(_mentions(_INVISIBLE.sub("", s)))  # invisible characters out first: `jane<ZWSP>.doe@` is one address
    s = _SPACE_ENTITY.sub(" ", s).replace(" ", " ").replace("\t", " ")  # no [ \t] left for `_text` to backtrack over
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _emails(_INVISIBLE.sub("", _ZS.sub(" ", dod._text(s))))  # again: entity-encoded and span-split addresses only exist now
    lines = []
    for line in s.splitlines():
        line = _CONTROL.sub(" ", line)
        m = _LEAD.match(line)
        line = line[m.end():] if m else line
        m = _LIST_NUMBER.match(line)
        line = " ".join((line[m.end():] if m else line).split())
        if line:
            lines.append(line)
    return lines, problems


# ---- parse ----

_START = re.compile(r"rule\s+([0-9]+)\s*:", re.I)
_NO_NUMBER = re.compile(r"rule\s*[:：]", re.I)
_TOKEN = re.compile(r"rule\s+([^\s:]+)\s*:", re.I)
_BAD_LINE = re.compile(r"rule\s*(?:[0-9]|[.\-–—]\s*[0-9])", re.I)
_STATUS_LINE = re.compile(r"status\s*:", re.I)
_STATUS = re.compile(r"status\s*:\s*(approved|draft|retired)\.?$", re.I)
_BAD_STATUS = re.compile(r"status\s*(?:[^\w\s:]|(?:approved|draft|retired)(?![A-Za-z0-9]))", re.I)  # any punctuation but `:` after the word
_LABELS = {k: re.compile(k + r"\s*:", re.I) for k in ("why", "example", "owner")}


_LABEL = re.compile(r"(why|example|owner)\s*:\s*", re.I)


def rule_hash(n, status, body):
    """8 hex over the number, the status value and the body: body[0] (the statement) and the other lines with whitespace collapsed
    and `Why :` / `WHY:` read as `why:`. Valid Status lines are left out (the value is already in the prefix), so their spelling
    and position never change the hash."""
    rest = [_LABEL.sub(lambda m: m.group(1).lower() + ": ", x, count=1) for x in body[1:] if not _STATUS.match(x)]
    return decisions.line_hash(f"{n}|{status}|" + " ".join(body[:1] + rest))


_MARKER = re.compile(r"[^\w\s]*\w?[^\w\s]*|[0-9]+(?:[.)][0-9]*)*[.)]?")  # a bullet, a one-character token (`Ø`, `v`, `a)`), `1.1`
_SYMBOL = re.compile(r"[^\w\s]+")
_RULE_WORD = re.compile(r"(?<![A-Za-z0-9])rule\s*\d", re.I)
_RULE_LABEL = re.compile(r"rule\s*\d[^\s:\uff1a]{0,10}\s*[:\uff1a]", re.I)  # bounded: no backtracking over a long token
_SECOND_RULE = re.compile(r"(?<![A-Za-z0-9])rule\s*\d+\s*[:\uff1a]", re.I)


def _behind_marker(line):
    """`Rule n: ...` behind a Word/Wingdings bullet or other short marker (`Ø Rule 2:`, `a) Rule 2:`, `1 | Rule 2:`, `> Rule 2:`).
    Prose (`See Rule 2`, `the rule 2 times`) has a word before it and is not flagged."""
    head = line[:100]
    m = _RULE_WORD.search(head)
    if not m or not m.start():
        return False
    tokens = head[:m.start()].split()
    if not tokens or len(tokens) > 3 or not all(_MARKER.fullmatch(t) for t in tokens):
        return False
    return all(_SYMBOL.fullmatch(t) for t in tokens) or bool(_RULE_LABEL.match(head, m.start()))


def _classify(line):
    """None (ordinary text), ("rule", n, rest) or ("bad", issue). A bad Rule line still ends the rule above it, so its Status
    line can never be read as the neighbour's. Fullwidth letters and digits are matched through NFKC (the key only, the stored
    text is untouched) and are an error, not a rule."""
    found = _classify_start(line)
    if found is None and (_behind_marker(line) or (not line[:120].isascii()
                                                  and _classify_start(unicodedata.normalize("NFKC", line[:120])) is not None)):
        return ("bad", "bad-rule-line")
    return found


def _classify_start(line):
    if line[:4].lower() != "rule":
        return None
    m = _START.match(line)
    if m:
        digits = m.group(1).lstrip("0")
        if not digits or len(digits) > 4 or int(digits) > 9999:  # len first: int() of a huge digit run raises on 3.11+
            return ("bad", "bad-number")
        return ("rule", int(digits), line[m.end():].strip())
    if _NO_NUMBER.match(line):
        return ("bad", "no-number")
    m = _TOKEN.match(line)
    if m and re.search(r"\d", m.group(1)):
        return ("bad", "bad-number")
    if _BAD_LINE.match(line):
        return ("bad", "bad-rule-line")
    return None


def _build(wid, n, rest, lines):
    """One rule: (R, problems). Status errors leave the status `draft`, which binds nobody."""
    rid = f"BR-{wid}-{n}"
    body = [rest] + lines
    probs, status = [], "draft"
    if _SECOND_RULE.search(rest):  # `Rule 1: a. Rule 2: b.` on one line: the second one would be lost inside the first
        probs.append("bad-rule-line")
    marked = [x for x in lines if _STATUS_LINE.match(x)]
    valid = [_STATUS.match(x) for x in marked]
    if len(marked) > 1:
        probs.append("two-status")
    if not all(valid):  # `Status:` followed by anything but a status word
        probs.append("unknown-status")
    if len(marked) == 1 and valid[0]:
        status = valid[0].group(1).lower()
    bad = any(_BAD_STATUS.match(x) for x in lines if not _STATUS_LINE.match(x))
    if bad:
        probs.append("bad-status-line")
    if not marked and not bad:
        probs.append("no-status")
    if len(" ".join(body)) > MAX_RULE:
        probs.append("rule-too-long")
    for k, label in (("why", "no-why"), ("example", "no-example"), ("owner", "no-owner")):
        if not any(_LABELS[k].match(x) for x in lines):
            probs.append(label)
    return {"id": rid, "n": n, "status": status, "body": body, "hash": rule_hash(n, status, body)}, [_problem(rid, i) for i in probs]


def parse(lines, wid, problems=()):
    """Normalised lines -> {"found", "rules", "duplicates", "problems", "notes"}.
    `rules`: R = {"id": "BR-<wid>-<n>", "n", "status", "body", "hash"}, ordered as written; body[0] is the text after `Rule <n>:` (maybe
    empty) and the rest are the following lines verbatim, up to the next rule start. A number written twice is in neither `rules`
    nor citable: both copies are in `duplicates`. `found` is true when there is a heading or any rule-looking line, so a problem
    is never hidden behind "no section"."""
    if type(wid) is not int or not 0 < wid < 10 ** 9:
        raise ValueError("wid must be a work item id")
    out = {"found": False, "rules": [], "duplicates": [], "problems": [dict(p) for p in problems], "notes": []}
    probs = out["problems"]
    kinds = [_classify(x) for x in lines]
    head = next((i for i, x in enumerate(lines) if x.casefold() in ("business rules", "business rules:")), None)
    if any(kinds[:head]):  # a rule-looking line above the heading (or with no heading at all)
        probs.append(_problem(None, "no-section-heading"))
        out["found"] = True
    if head is None:
        return out
    out["found"] = True
    section, kinds = lines[head + 1:], kinds[head + 1:]
    if sum(len(x) + 1 for x in section) > MAX_SECTION or sum(1 for k in kinds if k and k[0] == "rule") > MAX_RULES:
        probs.append(_problem(None, "section-too-big"))
        return out
    blocks, intro = [], False
    for x, k in zip(section, kinds):
        if k:
            blocks.append([k, []])
        elif blocks:
            blocks[-1][1].append(x)
        else:
            intro = True
    if intro:
        out["notes"].append("text between the Business rules heading and the first rule was ignored")
    counts = {}
    for k, _ in blocks:
        if k[0] == "rule":
            counts[k[1]] = counts.get(k[1], 0) + 1
    if section and not counts:
        probs.append(_problem(None, "no-parsable-rules"))
    elif not section:
        out["notes"].append("the Business rules section is empty")
    flagged = set()
    for k, body in blocks:
        if k[0] == "bad":
            probs.append(_problem(None, k[1]))
            continue
        r, found = _build(wid, k[1], k[2], body)
        if counts[k[1]] > 1:
            out["duplicates"].append(r)
            if k[1] not in flagged:
                flagged.add(k[1])
                probs.append(_problem(r["id"], "duplicate-number"))
            continue
        out["rules"].append(r)
        probs.extend(found)
    return out
