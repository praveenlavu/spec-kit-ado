import hashlib
import html as _html
import os
import re
import urllib.parse
from pathlib import Path

from .stack import CAP_TEXT, _clean  # stack imports only config at module level: no cycle


def _list_item(stack):
    """re.sub callback for <ol>/<ul>/<li> tags: `n. ` inside an <ol>, `- ` otherwise.
    shortcut: <ol start=…> is ignored (always counts from 1), honour it if ADO stories use it."""
    def tok(m):
        close, tag = m.group(1), m.group(2).lower()
        if tag != "li":
            if not close:
                stack.append(0 if tag == "ol" else None)
            elif stack:
                stack.pop()
        elif not close:
            if stack and stack[-1] is not None:
                stack[-1] += 1
                return f"\n{stack[-1]}. "
            return "\n- "
        return "\n"
    return tok


def _text(raw):
    """Convert ADO rich-text (HTML) to readable plain text. Deterministic."""
    if not raw:
        return ""
    s = raw.replace("\r", "")
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"(?i)<\s*(/?)\s*(ol|ul|li)\b[^>]*>", _list_item([]), s)
    s = re.sub(r"(?i)<\s*/\s*t[dh]\s*>\s*(?=<\s*t[dh][\s>])", " | ", s)  # cell separator
    s = re.sub(r"(?i)<\s*/?\s*(p|div|tr|h[1-6])\b[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


# Escaping for ADO text written into the ledger (§1), so story text can never open a fence or comment
# that hides later sections, or forge a `## ` heading. One scheme, per line: a leading `\` before a
# line-start ``` / ~~~ / # (after indent and one list marker), and `<!--` -> `&lt;!--` anywhere.
# `_unescape` is its inverse for `ac_lines` (lossy only for ADO text that itself starts a line with `\` + fence/# or contains `&lt;!--`); `ac_hash` hashes the raw ADO field, never this output.
_CTRL = re.compile(r"^([ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?)(?=```|~~~|#)", re.M)
_ESCAPED = re.compile(r"^\\(?=```|~~~|#)|&lt;!--")


def _esc(text):
    text = "\n".join(text.splitlines())  # str.splitlines (U+2028, \x0c, \r ...) is how prs._visible sees lines
    return _CTRL.sub(lambda m: m.group(1) + "\\", text).replace("<!--", "&lt;!--")


def _unescape(line):
    return _ESCAPED.sub(lambda m: "<!--" if m.group(0) != "\\" else "", line)


def _hv(value):
    """A value for a ledger header line: one line (any line break becomes a space) and escaped like the title, so it can never open a comment."""
    return _esc(" ".join(str(value).splitlines()))


def _ado_url(organization, project, work_item_id):
    proj = urllib.parse.quote(project)
    return f"https://dev.azure.com/{organization}/{proj}/_workitems/edit/{work_item_id}"


# Everything from this marker onward is authored content preserved across --refresh.
SECTION2 = "## 2. Brainstorm & plan"
_SECTION2_RE = re.compile(r"^" + re.escape(SECTION2), re.M)
_FENCE_OPEN = re.compile(r"^ {0,3}(?:(`{3,})[^`]*$|(~{3,}))")


def _scan(md, fill):
    """([md's lines with what renders as code or not at all replaced by `fill` per character],
    whether a fence or comment is still open at the end). Blanked: fenced code (CommonMark: closed
    only by the same character, at least as long, nothing but whitespace after) and <!-- --> comments."""
    out, fence, comment = [], None, False
    for line in md.splitlines():
        if fence:
            if re.fullmatch(rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*", line):
                fence = None
            out.append(fill * len(line))
            continue
        m = None if comment else _FENCE_OPEN.match(line)
        if m:
            fence = m.group(1) or m.group(2)
            out.append(fill * len(line))
            continue
        kept, rest = "", line
        while rest:
            if comment:
                end = rest.find("-->")
                if end < 0:
                    kept += fill * len(rest)
                    break
                comment, kept, rest = False, kept + fill * (end + 3), rest[end + 3:]
            else:
                start = rest.find("<!--")
                if start < 0:
                    kept += rest
                    break
                kept, rest, comment = kept + rest[:start] + fill * 4, rest[start + 4:], True
        out.append(kept)
    return out, bool(fence or comment)


def _visible(md):
    """(md with fences and comments removed; lines stay 1:1 with md.splitlines(), whether one is still open)."""
    out, still_open = _scan(md, "")
    return "\n".join(out), still_open


def _mask(md):
    """md with fences and comments overwritten by spaces: same length as md, so an offset found here
    slices the original. Every line break becomes `\\n` (CRLF is kept), as `re.M` and `_visible` see lines."""
    out, _ = _scan(md, " ")
    return "".join(k + ("\r\n" if raw.endswith("\r\n") else "\n" if raw[len(k):] else "")
                   for k, raw in zip(out, md.splitlines(keepends=True)))


def unclosed(md):
    """True when a fence or comment is left open: everything after it is hidden, so callers fail closed."""
    return _scan(md, "")[1]


def _section2_at(md):
    """Offset of the LAST line-anchored SECTION2 heading outside fences and comments, or -1. Inline
    mentions never count, and a forged heading copied into section 1 loses to the real one rendered after it."""
    last = None
    for last in _SECTION2_RE.finditer(_mask(md)):
        pass
    return last.start() if last else -1


def _repair_at(md):
    """Offset to splice from when `md` has an unclosed opener, else -1. One rule, no search: the only splice
    point is the FIRST raw line-start SECTION2 heading (section 1 ends there in a real ledger), and only when the
    text before it is unclosed (the opener is in section 1: old ledgers, before story text was escaped) and the
    suffix from it is balanced and still has `## 3.`, `## 4.`, `## 5.` in order. Anything else refuses."""
    m = _SECTION2_RE.search(md)
    if (m and unclosed(md[:m.start()]) and not unclosed(md[m.start():])
            and re.search(r"^## 3\..*?^## 4\..*?^## 5\.", _mask(md[m.start():]), re.M | re.S)):
        return m.start()
    return -1


def section_span(ledger_md, n):
    """(start, end) offsets of the body of `## <n>.` (up to the next `## ` heading or the end), searched only after the
    last SECTION2 marker, so story text copied into section 1 can never stand in for it. Headings inside fences or
    comments are not headings. None if absent."""
    vis = _mask(ledger_md)
    i = _section2_at(ledger_md)
    m = re.search(rf"^## {n}\.", vis[i:], re.M) if i != -1 else None
    if not m:
        return None
    start = i + m.end()
    nxt = re.search(r"^## ", vis[start:], re.M)
    return start, (start + nxt.start() if nxt else len(ledger_md))


def section(ledger_md, n):
    """Body of `## <n>.` (see section_span; the returned text is the authored original). None if absent."""
    span = section_span(ledger_md, n)
    return ledger_md[span[0]:span[1]] if span else None


_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])(?:\s+|$)")  # `$`: _text leaves a bare marker line before <li><div>
_AC_NONE = re.compile(r"^_\(none[^)]*\)_$")  # the placeholder render_dod writes when ADO has no AC


def ac_lines(ledger_md):
    """The AC lines of section 1's `### Acceptance Criteria` block; AC-n is the n-th.
    Blank lines, blockquote lines (the thin-AC banner) and the `_(none ...)_` placeholder are
    skipped; leading list markers are stripped. Read only before SECTION2, like `section`."""
    vis, i = _mask(ledger_md), _section2_at(ledger_md)
    m = re.search(r"^### Acceptance Criteria[ \t]*$", vis[:i] if i != -1 else vis, re.M)
    if not m:
        return []
    end = i if i != -1 else len(vis)
    nxt = re.search(r"^##", vis[m.end():end], re.M)
    return _ac_line_list(ledger_md[m.end():m.end() + nxt.start() if nxt else end].splitlines())


def _ac_line_list(lines):
    out = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith(">") and not _AC_NONE.match(line):
            if line := _unescape(_LIST_MARKER.sub("", line)):
                out.append(line)
    return out


# Real stories on the board run 300+ chars of AC; below this it cannot be
# decomposed into AC-1…AC-n without inventing requirements. Advisory only.
AC_THIN_CHARS = 150


def _thin(n_chars):
    return n_chars < AC_THIN_CHARS


def ac_stats(work_item):
    """AC length (the one rule: sum of `ac_lines` lengths, markers stripped) + whether it is too
    thin to decompose. Never fabricates."""
    raw = work_item.get("fields", {}).get("Microsoft.VSTS.Common.AcceptanceCriteria", "")
    n = sum(map(len, _ac_line_list(_text(raw).splitlines())))
    return {"ac_chars": n, "ac_thin": _thin(n)}


_AC_FIELD = "Microsoft.VSTS.Common.AcceptanceCriteria"
SPEC_DIR_UNSET = "_(set by speckit.ado.pull)_"


def ac_hash(work_item):
    """sha256 of the plain-text AC — markup-insensitive, so only real edits change it."""
    return hashlib.sha256(_text(work_item.get("fields", {}).get(_AC_FIELD, "")).encode("utf-8")).hexdigest()


def _header_value(ledger_md, label):
    m = re.search(rf"^- \*\*{re.escape(label)}:\*\*[ \t]*(.*?)[ \t]*$", ledger_md, re.M)
    return m.group(1) if m else None


def ledger_ac_hash(ledger_md):
    return _header_value(ledger_md, "AC hash") or None


def ledger_spec_dir(ledger_md):
    v = _header_value(ledger_md, "Spec dir")
    return None if not v or v == SPEC_DIR_UNSET else v


_H1 = re.compile(r"^## ", re.M)


def _header(md):
    """(the header block as lines, whether it can be trusted). The block is what comes before the first line-start `## ` heading
    outside fences and comments; text hidden in them is gone. Untrusted when the first raw line-start `## ` is not that heading (a comment
    or fence hides it: the opener sits in the header, e.g. an old title that was not escaped, and story text could stand in for header
    lines), or when there is no heading and a fence or comment is still open. The readers below call that invalid."""
    vis, still_open = _visible(md)
    m = _H1.search(vis)
    raw = next((i for i, line in enumerate(md.splitlines()) if line.startswith("## ")), None)
    shown = vis.count("\n", 0, m.start()) if m else None
    return (vis[:m.start()] if m else vis).splitlines(), raw == shown and (bool(m) or not still_open)


def header_block(md):
    """The text above the first `## ` heading outside fences and comments (fences and comments removed). The only place `Rules from:` and
    `Rules reviewed:` are read, so story text in section 1 can never stand in for them."""
    return "".join(line + "\n" for line in _header(md)[0])


_RULES_ID = r"[1-9][0-9]{0,8}"
_RULES_FROM = re.compile(rf"none|{_RULES_ID}(?:, {_RULES_ID})*")
MAX_RULES_FROM = 3  # the walk follows at most 3 links


def _look(label):
    """A line that names `label` as a header line in any spelling (list marker, blockquote prefix, emphasis, case)."""
    return re.compile(r"\s*(?:>\s*)*(?:(?:[-*+]|[0-9]+[.)])\s*)?[*_]*\s*" + label.replace(" ", r"\s+") + r"\b", re.I)


def _labelled(md, label):
    """(present, value) for the header line `- **<label>:** <value>`. Exact form only (one space, no trailing blanks). Value None = malformed:
    a second line, an unclosed opener in the header, or a list line that names the label in any other way. A title or `Spec dir` that
    mentions the label is not a line of that kind (it does not begin with the label)."""
    lines, trusted = _header(md)
    hits = [x for x in lines if _look(label).match(x)]
    prefix = f"- **{label}:** "
    if not trusted:
        return True, None
    if not hits:
        return False, ""
    value = hits[0][len(prefix):]
    return True, value if len(hits) == 1 and hits[0].startswith(prefix) and value and value == value.strip() else None


def _parse_from(value):
    if not _RULES_FROM.fullmatch(value or ""):
        return None
    ids = [] if value == "none" else [int(t) for t in value.split(", ")]
    return ids if len(ids) <= MAX_RULES_FROM and len(set(ids)) == len(ids) else None


def _parse_reviewed(value):
    """{rule id: hash8} for `BR-1201-1@3f2a9c1b, ...` or `none`; None when malformed or an id repeats."""
    from .rules import TAG_RE  # rules imports this module at load time: import lazily
    if value == "none":
        return {}
    out = {}
    for tok in (value or "x").split(", "):
        rid, at, h = tok.partition("@")
        if not (at and TAG_RE.fullmatch(rid) and re.fullmatch(r"[0-9a-f]{8}", h)) or rid in out:
            return None
        out[rid] = h
    return out


def ledger_rules_from(md):
    """`{"present", "valid", "ids"}` for `- **Rules from:** 1201, 1200` (nearest first) or `none`, read from the header block only. Invalid
    (and `ids == []`): more than 3 ids, a repeated id, an id that is not 1 to 9 digits without a leading zero, a second line, any other spelling
    of the line. Absent is `present False, valid True`."""
    present, value = _labelled(md, "Rules from")
    ids = _parse_from(value) if present else []
    return {"present": present, "valid": ids is not None, "ids": ids or []}


def ledger_rules_reviewed(md):
    """`{"present", "valid", "hashes"}` for `- **Rules reviewed:** BR-1201-1@3f2a9c1b, ...` (or `none`), read from the header block only. The same
    rules as `ledger_rules_from`; ids match `rules.TAG_RE`, hashes are 8 lowercase hex, an id may appear once."""
    present, value = _labelled(md, "Rules reviewed")
    hashes = _parse_reviewed(value) if present else {}
    return {"present": present, "valid": hashes is not None, "hashes": hashes or {}}


MAX_EXTRA_DOD = 20


def stack_dod_items(profile):
    """`(stack id, item, source)` for every DoD item of an approved profile, in stack order (`stack.load_profile` returns only approved ones)."""
    return [(st.get("id"), i.get("item"), i.get("source")) for st in (profile or {}).get("stacks", []) for i in st.get("dod", [])]


def _extra_dod_lines(extra_dod):
    """Section 5 lines for stack DoD items: cleaned (control, separator and invisible characters, `<!--`, `-->`, fences), escaped, at most
    MAX_EXTRA_DOD; more than that adds one fixed line instead of dropping items silently."""
    extra = list(extra_dod)
    lines = [f"- [ ] {_esc(_clean(item))} (stack {_clean(sid, 40)}, {_clean(src, 12)})" for sid, item, src in extra[:MAX_EXTRA_DOD]]
    if len(extra) > MAX_EXTRA_DOD:
        lines.append(f"- [ ] {len(extra) - MAX_EXTRA_DOD} more stack DoD items were not seeded: run `ado stack show --md`")
    return lines


def render_dod(work_item, organization, project, synced="", spec_dir="", extra_dod=(), rules_from=None, rules_reviewed=""):
    """`rules_from`: None writes no `Rules from:` line (the baseline); a list of ids writes it (`none` when empty). `rules_reviewed`: the value
    of a `Rules reviewed:` line, written when not empty. Both are checked with the readers' own rules before they reach the header."""
    if "\n" in spec_dir or "\r" in spec_dir:
        raise ValueError("spec dir must be a single line (it is written into the ledger header)")
    spec_dir = _hv(spec_dir)
    header = []
    if rules_from is not None:
        if not isinstance(rules_from, (list, tuple)) or any(type(i) is not int for i in rules_from):
            raise ValueError("rules_from must be a list of work item ids")
        value = ", ".join(map(str, rules_from)) or "none"
        if _parse_from(value) is None:
            raise ValueError("rules_from must hold at most 3 different work item ids of 1 to 9 digits")
        header.append(f"- **Rules from:** {value}")
    if rules_reviewed:
        if _parse_reviewed(rules_reviewed) is None:
            raise ValueError("rules_reviewed must be BR-<id>-<n>@<8 hex> entries separated by ', ', each rule once")
        header.append(f"- **Rules reviewed:** {rules_reviewed}")
    wid = work_item["id"]
    f = work_item.get("fields", {})
    title = f.get("System.Title", "")
    wtype = _hv(f.get("System.WorkItemType", ""))
    state = _hv(f.get("System.State", ""))
    ac = _text(f.get("Microsoft.VSTS.Common.AcceptanceCriteria", ""))
    desc = _text(f.get("System.Description", ""))
    repro = _text(f.get("Microsoft.VSTS.TCM.ReproSteps", ""))

    n_ac = sum(map(len, _ac_line_list(ac.splitlines())))  # counted before escaping: human text only
    ac_block = _esc(ac) or "_(none in ADO — improve the story)_"
    desc, repro = _esc(desc), _esc(repro)
    if _thin(n_ac):
        ac_block = (
            f"> **Thin acceptance criteria** ({n_ac} chars). Splitting this into "
            "AC-1…AC-n is judgment, not transcription — record the assumptions and open "
            "questions it forces in section 2, and consider improving the story in ADO "
            "first.\n\n" + ac_block
        )
    parts = [
        f"# DoD — US{wid}: {_hv(title)}",  # a line break in the title would let `\r```` start a fence line
        "",
        f"- **ADO:** {_hv(_ado_url(organization, project, wid))}",
        f"- **Type / State:** {wtype} / {state}",
        f"- **Last synced:** {_hv(synced)}",
        f"- **AC hash:** {ac_hash(work_item)}",
        f"- **Spec dir:** {spec_dir or SPEC_DIR_UNSET}",
        *header,
        "",
        f"## 1. Seeded from ADO (verbatim text, ADO HTML converted to plain text — do not edit; refresh via `ado dod {wid} --refresh`)",
        "",
        "### Acceptance Criteria",
        ac_block,
        "",
        "### Description",
        desc or "_(none)_",
    ]
    if repro:
        parts += ["", "### Repro Steps", repro]
    parts += [
        "",
        SECTION2 + "  (fill before building — per discipline)",
        "- **Approach / chosen option + why:** _(fill in)_",
        "- **Assumptions:** _(fill in)_ "
        f"Each is also recorded in Decisions_{wid}.md (`ado decisions {wid} --add`).",
        "- **Open questions → answers:** _(fill in as they arise)_",
        "  Format, one line each: `- [ ] Q1: <question> → _(open)_ · owner: PO`; answered: `- [x] Q1: <question> → <answer> · owner: PO`",
        "- **Use cases & edge cases:** _(fill in — each: covered-by test / N/A + why)_",
        "",
        "## 3. AC → tests (traceability)  (tag each AC as AC-1, AC-2…)",
        "",
        "Tests are free-form IDs: `Class.method`, `path::test_name` or `describe > it`.",
        "",
        "| AC | acceptance criterion | tests | commit/PR | status |",
        "|---|---|---|---|---|",
        "| AC-1 | _(what it must do)_ | _(test IDs, …)_ | _(commit / PR)_ | _(red→green)_ |",
        "",
        "## 4. Gate & review log  (appended by speckit.ado.gate and speckit.ado.review)",
        "_(none yet)_",
        "",
        "## 5. Definition of Done  (all ✅ to close)",
        "- [ ] every AC mapped to a passing real test",
        "- [ ] coverage meets the project's bar",
        "- [ ] no assertion-free tests",
        "- [ ] all assumptions / questions / edge cases resolved",
        "- [ ] gates green",
        *_extra_dod_lines(extra_dod),
        "",
    ]
    return "\n".join(parts)


class DodExists(Exception):
    """Ledger already exists and neither --force nor --refresh was given."""


def dod_file(dod_dir, work_item_id):
    return Path(dod_dir) / f"DoD_{work_item_id}.md"


def write_dod(work_item, dod_dir, organization, project, synced="",
              force=False, refresh=False, spec_dir="", extra_dod=(), rules_from=None, rules_reviewed=""):
    path = dod_file(dod_dir, work_item["id"])
    if path.exists() and not force and not refresh:
        raise DodExists(f"{path} exists — pass --force to overwrite or --refresh to re-pull section 1.")
    old = path.read_text(encoding="utf-8") if refresh and path.exists() else None
    if old is not None:
        spec_dir = spec_dir or ledger_spec_dir(old) or ""
        # written by `ado rules --confirm`: kept like Spec dir, even when rules are off. A header that cannot be trusted (an old ledger's
        # unescaped `<!--` in its title hid the first heading) cannot be read: with no line that names the label it is rebuilt as before,
        # so teams not using rules see no change; with one (maybe forged from the Description) the refresh refuses.
        if _header(old)[1]:
            present, kept = _labelled(old, "Rules reviewed")
        else:
            present, kept = any(_look("Rules reviewed").match(x) for x in old.splitlines()), None
        if present and _parse_reviewed(kept) is None:
            raise DodExists(f"{path} has a malformed or untrusted 'Rules reviewed:' header line — refusing to --refresh "
                            "(it would drop the hashes a human confirmed, or copy a forged line). Fix the header, or use --force to fully overwrite.")
        rules_reviewed = rules_reviewed or kept or ""
    # stack DoD items seed a fresh ledger only: a refresh keeps everything after section 1, section 5 included
    fresh = render_dod(work_item, organization, project, synced, spec_dir, () if old is not None else extra_dod, rules_from, rules_reviewed)
    if old is not None:
        idx = _repair_at(old) if unclosed(old) else _section2_at(old)
        if unclosed(old) and idx == -1:
            raise DodExists(
                f"{path} has an unclosed code fence or comment outside section 1 — refusing to --refresh "
                "(it would splice from the wrong place). Close it, or use --force to fully overwrite."
            )
        if idx == -1:
            raise DodExists(
                f"{path} is missing the '{SECTION2}' marker — refusing to --refresh "
                "(would discard authored content). Fix the file, or use --force to fully overwrite."
            )
        # keep freshly-rendered header+section1, splice old section2..end
        head = fresh[:_section2_at(fresh)]
        fresh = head + old[idx:]
    path.parent.mkdir(parents=True, exist_ok=True)
    # write-then-rename so a crash or disk-full mid-write never truncates authored sections
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(fresh, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path
