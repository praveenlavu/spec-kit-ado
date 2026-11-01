import os

from ado.dod import ac_lines, ac_hash, ledger_ac_hash, render_dod, _text, SECTION2


def _wi(**fields):
    base = {"System.Title": "T", "System.WorkItemType": "User Story", "System.State": "New"}
    base.update(fields)
    return {"id": 1001, "fields": base}


def test_text_strips_html_and_unescapes():
    assert _text("<div>As a user</div><br>line2 &amp; more") == "As a user\nline2 & more"
    assert _text("<ul><li>a</li><li>b</li></ul>") == "- a\n- b"
    assert _text("") == ""


def test_render_seeds_ac_verbatim_as_text():
    md = render_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "<div>AC one</div>"}),
                    "contoso", "Fabrikam", synced="2026-07-14")
    assert "# DoD — US1001: T" in md
    assert "https://dev.azure.com/contoso/Fabrikam/_workitems/edit/1001" in md
    assert "Last synced:** 2026-07-14" in md
    assert "AC one" in md                      # AC text present
    assert "<div>" not in md                    # HTML stripped


def test_render_has_all_five_sections():
    md = render_dod(_wi(), "o", "p", synced="2026-07-14")
    for marker in ["## 1. Seeded from ADO", "## 2. Brainstorm & plan",
                   "## 3. AC", "## 4. Gate & review log", "## 5. Definition of Done"]:
        assert marker in md
    assert "coverage meets the project's bar" in md
    assert "≥ 90%" not in md


def test_render_is_deterministic():
    a = render_dod(_wi(), "o", "p", synced="2026-07-14")
    b = render_dod(_wi(), "o", "p", synced="2026-07-14")
    assert a == b


def test_render_handles_missing_ac():
    md = render_dod(_wi(), "o", "p", synced="2026-07-14")
    assert "_(none in ADO — improve the story)_" in md


from ado.dod import AC_THIN_CHARS, ac_stats

_FULL_AC = (
    "Given a user with an active plan, when the nightly job runs, then eligibility "
    "is recalculated and written back to the record. Given a lapsed plan, the user "
    "is excluded and an audit row is written."
)


def test_thin_ac_gets_callout_and_stays_verbatim():
    """AC-1: below the floor -> callout naming the count; AC still rendered verbatim."""
    md = render_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "should work"}),
                    "o", "p", synced="2026-07-14")
    assert "Thin acceptance criteria" in md
    assert "11 chars" in md          # the measured count is named
    assert "should work" in md       # verbatim seeding is unchanged


def test_full_ac_has_no_callout():
    """AC-2: at/above the floor -> no callout, rendering otherwise unchanged."""
    assert len(_FULL_AC) >= AC_THIN_CHARS
    md = render_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": _FULL_AC}),
                    "o", "p", synced="2026-07-14")
    assert "Thin acceptance criteria" not in md
    assert "eligibility is recalculated" in md


def test_empty_ac_keeps_placeholder_and_is_thin():
    """AC-3: empty -> existing placeholder preserved AND flagged."""
    md = render_dod(_wi(), "o", "p", synced="2026-07-14")
    assert "_(none in ADO — improve the story)_" in md
    assert "Thin acceptance criteria" in md
    assert ac_stats(_wi())["ac_thin"] is True


def test_ac_stats_measures_stripped_text_not_markup():
    """AC-4: HTML markup can't inflate a thin story past the floor."""
    padded = "<div class='" + "x" * 400 + "'>too short</div>"
    stats = ac_stats(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": padded}))
    assert stats["ac_chars"] == len("too short")
    assert stats["ac_thin"] is True


def test_ac_stats_reports_full_ac_as_not_thin():
    stats = ac_stats(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": _FULL_AC}))
    assert stats["ac_thin"] is False
    assert stats["ac_chars"] == len(_FULL_AC)


import pytest
from ado.dod import write_dod, dod_file, DodExists


def test_write_creates_file(tmp_path):
    p = write_dod(_wi(), str(tmp_path), "o", "p", synced="2026-07-14")
    assert p == dod_file(str(tmp_path), 1001)
    assert p.name == "DoD_1001.md"
    assert "## 1. Seeded from ADO" in p.read_text()


def test_write_refuses_clobber_without_force(tmp_path):
    write_dod(_wi(), str(tmp_path), "o", "p", synced="2026-07-14")
    with pytest.raises(DodExists, match="--force"):
        write_dod(_wi(), str(tmp_path), "o", "p", synced="2026-07-14")


def test_force_overwrites(tmp_path):
    write_dod(_wi(**{"System.Title": "old"}), str(tmp_path), "o", "p", synced="2026-07-14")
    p = write_dod(_wi(**{"System.Title": "new"}), str(tmp_path), "o", "p",
                  synced="2026-07-14", force=True)
    assert "US1001: new" in p.read_text()


def test_refresh_replaces_section1_preserves_authored(tmp_path):
    p = write_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "old AC"}),
                  str(tmp_path), "o", "p", synced="2026-07-14")
    # simulate authored content in section 2+
    text = p.read_text().replace("_(fill in)_", "MY REAL PLAN", 1)
    p.write_text(text)
    # refresh with new AC
    write_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "new AC"}),
              str(tmp_path), "o", "p", synced="2026-07-15", refresh=True)
    out = p.read_text()
    assert "new AC" in out and "old AC" not in out     # S1 refreshed
    assert "MY REAL PLAN" in out                        # S2+ preserved
    assert "Last synced:** 2026-07-15" in out           # header re-stamped


def test_refresh_refuses_when_marker_missing(tmp_path):
    write_dod(_wi(), str(tmp_path), "o", "p", synced="2026-07-14")
    p = dod_file(str(tmp_path), 1001)
    p.write_text("hand-mangled file, no section-2 header\nMY WORK")
    with pytest.raises(DodExists, match="refusing to --refresh"):
        write_dod(_wi(), str(tmp_path), "o", "p", synced="2026-07-14", refresh=True)
    assert "MY WORK" in p.read_text()  # authored content preserved, not clobbered


def test_ledger_written_as_utf8(tmp_path):
    p = write_dod(_wi(), str(tmp_path), "o", "p", synced="2026-07-14")
    text = p.read_bytes().decode("utf-8")  # raises if not valid utf-8
    assert "—" in text and "≥" not in text


def test_render_section3_has_ac_tag_convention(tmp_path):
    md = render_dod({"id": 1, "fields": {"System.Title": "T"}}, "o", "p", synced="2026-07-14")
    assert "| AC | acceptance criterion | tests | commit/PR | status |" in md
    assert "AC-1" in md
    assert "tag each AC" in md


import hashlib
from ado.dod import ac_hash, ledger_ac_hash, ledger_spec_dir

_AC = "Microsoft.VSTS.Common.AcceptanceCriteria"


def test_ac_hash_stable_across_html_variants():
    a = ac_hash(_wi(**{_AC: "<p>A &amp; B</p>"}))
    b = ac_hash(_wi(**{_AC: "<div>A & B</div>"}))
    assert a == b


def test_ac_hash_empty():
    assert ac_hash(_wi()) == hashlib.sha256(b"").hexdigest()


def test_ac_hash_non_ascii():
    h = ac_hash(_wi(**{_AC: "Café ✓"}))
    assert h == ac_hash(_wi(**{_AC: "Café ✓"}))
    assert h == hashlib.sha256("Café ✓".encode("utf-8")).hexdigest()


def test_render_has_hash_and_spec_dir_lines():
    wi = _wi(**{_AC: "AC one"})
    md = render_dod(wi, "o", "p", synced="2026-07-14", spec_dir="specs/001-x")
    assert ledger_ac_hash(md) == ac_hash(wi)
    assert ledger_spec_dir(md) == "specs/001-x"
    # header lines sit above section 2 and the placeholder reads as None
    assert md.index("**AC hash:**") < md.index(SECTION2)
    unset = render_dod(wi, "o", "p", synced="2026-07-14")
    assert "- **Spec dir:** _(set by speckit.ado.pull)_" in unset
    assert ledger_spec_dir(unset) is None
    assert ledger_ac_hash("no header here") is None
    assert ledger_spec_dir("no header here") is None


def test_refresh_keeps_spec_dir_when_not_given(tmp_path):
    write_dod(_wi(**{_AC: "old"}), str(tmp_path), "o", "p", synced="d1", spec_dir="specs/001-x")
    p = write_dod(_wi(**{_AC: "new"}), str(tmp_path), "o", "p", synced="d2", refresh=True)
    out = p.read_text()
    assert ledger_spec_dir(out) == "specs/001-x"
    assert ledger_ac_hash(out) == ac_hash(_wi(**{_AC: "new"}))
    p = write_dod(_wi(**{_AC: "new"}), str(tmp_path), "o", "p", synced="d3",
                  refresh=True, spec_dir="specs/002-y")
    assert ledger_spec_dir(p.read_text()) == "specs/002-y"


def test_section3_has_evidence_column():
    md = render_dod(_wi(), "o", "p", synced="d")
    assert "| AC | acceptance criterion | tests | commit/PR | status |" in md
    assert "|---|---|---|---|---|" in md
    assert "`path::test_name`" in md and "`describe > it`" in md
    assert "## 4. Gate & review log" in md


@pytest.mark.parametrize("bad", ["a\nb", "a\rb"])
def test_multiline_spec_dir_rejected(tmp_path, bad):
    with pytest.raises(ValueError, match="spec"):
        write_dod(_wi(), str(tmp_path), "o", "p", synced="d", spec_dir=bad)
    assert not list(tmp_path.iterdir())


def test_refresh_adds_header_lines_to_legacy_ledger(tmp_path):
    p = dod_file(str(tmp_path), 1001)
    tail = SECTION2 + "  (fill)\nMY REAL PLAN\n\n## 3. AC\n| AC-1 | x | T.a | abc | green |\n"
    p.write_text("# DoD — US1001: T\n\n- **Last synced:** old\n\n## 1. Seeded\nold\n\n" + tail,
                 encoding="utf-8")
    write_dod(_wi(**{_AC: "new"}), str(tmp_path), "o", "p", synced="d2", refresh=True)
    out = p.read_text(encoding="utf-8")
    assert ledger_ac_hash(out) == ac_hash(_wi(**{_AC: "new"}))
    assert "- **Spec dir:** _(set by speckit.ado.pull)_" in out and ledger_spec_dir(out) is None
    assert out[out.index(SECTION2):] == tail


def test_refresh_splices_at_real_marker_when_section1_quotes_it(tmp_path):
    # PR #1 review fix A (fresh I2): the marker text inside story text must not move the splice
    trap = {"Microsoft.VSTS.Common.AcceptanceCriteria": SECTION2 + "<br>old AC"}
    p = write_dod(_wi(**trap), str(tmp_path), "o", "p", synced="d1")
    p.write_text(p.read_text().replace("_(fill in)_", "MY REAL PLAN", 1))
    trap["Microsoft.VSTS.Common.AcceptanceCriteria"] = SECTION2 + "<br>new AC"
    write_dod(_wi(**trap), str(tmp_path), "o", "p", synced="d2", refresh=True)
    out = p.read_text()
    assert "new AC" in out and "old AC" not in out and "MY REAL PLAN" in out
    assert out.count(SECTION2) == 2


# ---- PR #1 review fix I: ADO rich text keeps its structure as plain text ----
def test_text_table_cells_and_rows():
    html = ("<table><tr><th>Given</th><th>When</th></tr>"
            "<tr><td>a user</td>\n<td>they log in</td></tr></table>")
    assert _text(html) == "Given | When\na user | they log in"


def test_text_div_open_and_close_break_lines():
    assert _text("Line one<div>Line two</div>Line three") == "Line one\nLine two\nLine three"


def test_text_ordered_list_numbered():
    assert _text("<ol><li>First</li><li>Second</li></ol>") == "1. First\n2. Second"
    assert _text("<ol><li>A<ul><li>x</li></ul></li><li>B</li></ol><ul><li>c</li></ul>") == "1. A\n- x\n2. B\n- c"


# ---- fix round 2: the marker counts only as a line-anchored heading ----
def test_refresh_keeps_authored_text_that_mentions_the_marker_inline(tmp_path):
    p = write_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "old AC"}), str(tmp_path), "o", "p", synced="d1")
    text = p.read_text().replace("_(fill in)_", f"MY PLAN, see `{SECTION2}` above", 1)
    p.write_text(text)
    write_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "new AC"}), str(tmp_path), "o", "p",
              synced="d2", refresh=True)
    out = p.read_text()
    assert out[out.index("\n" + SECTION2):] == text[text.index("\n" + SECTION2):]  # byte-for-byte
    assert "new AC" in out and "old AC" not in out


# ---- F2: the ledger write is atomic; a failed --refresh never wipes authored sections ----
def test_refresh_failure_keeps_old_ledger(tmp_path, monkeypatch):
    p = write_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "old AC"}), str(tmp_path), "o", "p", synced="d1")
    before = p.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "new AC"}), str(tmp_path), "o", "p",
                  synced="d2", refresh=True)
    assert p.read_bytes() == before                    # original ledger untouched
    assert not list(tmp_path.glob("*.tmp"))           # no stray temp file left behind


def test_ac_lines_rule():
    def ac(raw):
        return ac_lines(render_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": raw}), "o", "p"))
    # markers stripped, blanks skipped; thin banner (blockquote) skipped
    assert ac("<ul><li>Given a</li><li>Then b</li></ul>") == ["Given a", "Then b"]
    assert ac("<ol><li>one</li><li>two</li></ol>") == ["one", "two"]
    assert ac("<p>* star</p><p>+ plus</p><p>3) paren</p><p>&nbsp;</p><p>plain</p>") == ["star", "plus", "paren", "plain"]
    assert render_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "x"}), "o", "p").count("> **Thin") == 1
    assert ac("x") == ["x"]
    # ADO has no AC -> placeholder, zero lines; also the plain `_(none)_` form
    assert ac("") == []
    assert ac_lines("## 1. S\n### Acceptance Criteria\n_(none)_\n\n### Description\nd\n") == []
    assert ac_lines("no ledger") == []


def test_ac_lines_read_the_same_from_a_crlf_ledger():
    """A Windows checkout (autocrlf) turns every `\\n` into `\\r\\n`; `$` in the heading pattern stopped at `\\n`, so AC-n vanished and the
    rules gate saw 0 criteria (found while reviewing P3-8)."""
    md = render_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Given a</li><li>Then b</li></ul>"}), "o", "p")
    assert ac_lines(md) == ["Given a", "Then b"]
    assert ac_lines(md.replace("\n", "\r\n")) == ["Given a", "Then b"]
    assert ac_lines(md.replace("\n", "\r")) == ["Given a", "Then b"]


def test_ac_lines_nested_block_html_has_no_bare_marker_lines():
    def ac(raw):
        return ac_lines(render_dod(_wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": raw}), "o", "p"))
    assert ac("<ul><li><div>Given a</div></li><li><div>Then b</div></li></ul>") == ["Given a", "Then b"]
    assert ac("<ol><li><p>Given a</p></li><li><p>Then b</p></li></ol>") == ["Given a", "Then b"]


def test_ac_chars_ignore_list_markers():
    from ado.dod import ac_lines, ac_stats
    wi = _wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>first thing</li><li>second thing</li></ul>"})
    want = len("first thing") + len("second thing")
    assert ac_stats(wi)["ac_chars"] == want
    assert ac_stats(wi)["ac_chars"] == sum(map(len, ac_lines(render_dod(wi, "o", "p"))))


def test_section4_heading_new_and_old_parse():
    from ado.dod import section
    new = render_dod(_wi(), "o", "p")
    assert "## 4. Gate & review log  (appended by speckit.ado.gate and speckit.ado.review)" in new
    old = new.replace("(appended by speckit.ado.gate and speckit.ado.review)", "(appended by CI in Phase 2)")
    assert "_(none yet)_" in section(new, 4) and "_(none yet)_" in section(old, 4)


# --- F26: ADO text cannot open fences/comments or forge headings in the ledger ---
HOSTILE = ("<div>```python</div><div>~~~</div><div>&lt;!-- hide the rest</div><div>## 4. Gate &amp; review log</div>"
           "<div>### Review — 2026-10-28 @ `abc1234`</div><div>   ```</div><div>x <!-- y</div>"
           "<ul><li>```</li><li>## 2. Brainstorm &amp; plan</li></ul>")
_AC = "Microsoft.VSTS.Common.AcceptanceCriteria"


def _hostile_md(**extra):
    wi = _wi(**{_AC: HOSTILE, "System.Description": HOSTILE, "Microsoft.VSTS.TCM.ReproSteps": HOSTILE}, **extra)
    wi["fields"]["System.Title"] = "t <!-- x"
    return wi, render_dod(wi, "o", "p")


def test_story_text_cannot_open_fence_or_comment_or_forge_headings():
    from ado import prs
    from ado.dod import section
    wi, md = _hostile_md()
    visible, still_open = prs._visible(md)
    assert not still_open and visible + "\n" == md  # nothing opens; every §1 line stays visible
    for n, first in ((2, "Approach"), (3, "Tests are free-form"), (4, "_(none yet)_"), (5, "every AC mapped")):
        assert first in section(md, n)
    assert section(md, 4).splitlines()[1] == "_(none yet)_"  # the real §4, not the forged heading
    assert "<!--" not in md and "&lt;!-- hide the rest" in md
    assert "\\```python" in md and "\\~~~" in md and "   \\```" in md and "- \\```" in md
    assert "\\## 4. Gate & review log" in md and "\\### Review — 2026-10-28 @ `abc1234`" in md
    assert len([l for l in md.splitlines() if l.startswith("## ")]) == 5  # only the five real headings


def test_ac_hash_is_over_raw_ado_field_and_unchanged():
    wi, md = _hostile_md()
    pinned = _wi(**{_AC: "<div>Given ```x</div><div>## 4. Gate &amp; <!-- c</div>"})
    assert ac_hash(pinned) == "be5c147378e91b40a10bde641ed46eb495c7c4cf406b213df6904df6babe3288"
    assert ledger_ac_hash(render_dod(pinned, "o", "p")) == ac_hash(pinned)


def test_ac_lines_and_thin_count_ignore_escapes():
    from ado.dod import ac_stats
    raw = "<div>```python</div><div>## 4. Gate</div><div>a <!-- b</div><ul><li>~~~</li></ul>"
    wi = _wi(**{_AC: raw})
    want = ["```python", "## 4. Gate", "a <!-- b", "~~~"]
    assert ac_lines(render_dod(wi, "o", "p")) == want
    assert ac_stats(wi)["ac_chars"] == sum(map(len, want))
    assert "Thin acceptance criteria** (%d chars)" % sum(map(len, want)) in render_dod(wi, "o", "p")


def test_unicode_and_control_line_breaks_cannot_open_fence():
    from ado import prs
    from ado.dod import section
    desc = "<div>a&#x2028;```</div><div>b&#x2029;~~~</div><div>c\x0c<!--</div><div>d\x85```</div>"
    wi = _wi(**{"System.Description": desc, _AC: "x&#x2028;```"})
    wi["fields"]["System.Title"] = "t\r```\n<!--"
    md = render_dod(wi, "o", "p")
    visible, still_open = prs._visible(md)
    assert not still_open and visible + "\n" == md.replace("\r", "\n")
    assert section(md, 4).splitlines()[1] == "_(none yet)_"
    assert prs._section2_line(md) == prs._section2_line(visible)
    assert md.splitlines()[0] == "# DoD — US1001: t ``` &lt;!--"  # title collapsed to one line, escaped
    assert ac_hash(wi) == ac_hash(_wi(**{_AC: "x&#x2028;```"}))  # hash is of the raw field only


# --- LEDGER5: a heading inside a fence or comment is never a heading ---
HIDERS = {"fence": ("```\n", "```\n"), "tilde": ("~~~\n", "~~~\n"), "comment": ("<!--\n", "-->\n")}


def _authored(hide=None, extra=""):
    """Real ledger whose §4 holds `hide`: a copy of the `## 2.` / `## 5.` headings inside a fence or comment."""
    o, c = HIDERS[hide] if hide else ("", "")
    copy = f"{o}{SECTION2}\nFAKE PLAN\n## 5. Definition of Done\n- [x] fake\n{c}" if hide else ""
    return (render_dod(_wi(), "o", "p").replace("_(none yet)_", "_(none yet)_\n" + copy + extra, 1)
            .replace("_(fill in)_", "MY REAL PLAN", 1))


@pytest.mark.parametrize("hide", HIDERS)
def test_section_and_marker_ignore_hidden_headings(hide):
    from ado.dod import _section2_at, section
    md = _authored(hide)
    assert md.index(SECTION2, _section2_at(md) + 1) > _section2_at(md)  # a later raw copy exists, and loses
    assert "MY REAL PLAN" in section(md, 2) and "FAKE PLAN" not in section(md, 2)
    assert "- [x] fake" in section(md, 4) and "- [x] fake" not in section(md, 5)  # authored text stays content of its section
    assert "every AC mapped" in section(md, 5)


@pytest.mark.parametrize("hide", HIDERS)
def test_refresh_keeps_sections_2_to_5_when_a_hidden_copy_of_the_marker_sits_later(tmp_path, hide):
    md = _authored(hide)
    p = tmp_path / "DoD_1001.md"
    p.write_text(md, encoding="utf-8")
    write_dod(_wi(**{_AC: "brand new AC"}), tmp_path, "o", "p", refresh=True)
    out = p.read_text(encoding="utf-8")
    assert out[out.index("\n" + SECTION2):] == md[md.index("\n" + SECTION2):]  # §2-§5 byte-equal
    assert "brand new AC" in out and "MY REAL PLAN" in out


def test_ac_lines_ignore_a_hidden_ac_heading_and_marker():
    md = render_dod(_wi(**{_AC: "real one"}), "o", "p")
    fenced = md.replace("### Acceptance Criteria", "```\n### Acceptance Criteria\nfake\n```\n### Acceptance Criteria", 1)
    assert ac_lines(fenced) == ["real one"]
    hidden_marker = md + f"<!--\n{SECTION2}\n-->\n"
    assert ac_lines(hidden_marker) == ["real one"]


@pytest.mark.parametrize("opener", ["```\n", "~~~\n", "<!--\n"])
def test_refresh_refuses_an_unclosed_opener(tmp_path, opener):
    md = _authored(extra=opener)
    p = tmp_path / "DoD_1001.md"
    p.write_text(md, encoding="utf-8")
    with pytest.raises(DodExists, match="unclosed code fence or comment"):
        write_dod(_wi(**{_AC: "new"}), tmp_path, "o", "p", refresh=True)
    assert p.read_text(encoding="utf-8") == md


def test_mask_is_length_preserving_and_visible_is_unchanged():
    from ado.dod import _mask, _visible
    for md in ("a\r\nb\n```\nx\r\n```\nc <!-- q\nr --> d\n", "x\x0cy ```\nz\n", "```\nunclosed", "t\n\n"):
        assert len(_mask(md)) == len(md)
        assert _visible(md)[0].count("\n") == len(md.splitlines()) - 1
    assert _mask("a <!-- b --> c\n```\nd\n```\n") == "a " + " " * 10 + " c\n" + " " * 3 + "\n \n" + " " * 3 + "\n"


# --- LEDGER5 fix rounds: --refresh repairs only an opener in §1, spliced at the FIRST raw marker ---
def _s1(opener, extra=""):
    """Old-style ledger (story text was not escaped yet): an unclosed `opener` in §1, a real plan in §2."""
    md = render_dod(_wi(), "o", "p").replace("_(fill in)_", "MY PLAN", 1)
    return md.replace("### Description\n_(none)_", f"### Description\n{extra}{opener}\nsee code", 1)


def _copy(md, opener=""):
    """A pasted copy of the §2 heading (after `opener`, if any) inside the real §2."""
    return md.replace("- **Assumptions:**", f"{opener}\n{SECTION2}\n- example\n- **Assumptions:**", 1)


def _refresh(tmp_path, md, **fields):
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    write_dod(_wi(**fields), tmp_path, "o", "p", refresh=True)
    return p.read_bytes().decode("utf-8")


def _refused(tmp_path, md):
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    with pytest.raises(DodExists, match="unclosed code fence or comment"):
        write_dod(_wi(), tmp_path, "o", "p", refresh=True)
    assert p.read_bytes().decode("utf-8") == md  # byte-unchanged: the real §2 text is never dropped


@pytest.mark.parametrize("crlf", [False, True])
@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_refresh_repairs_an_unclosed_opener_in_section_1(tmp_path, opener, crlf):
    from ado.dod import unclosed
    md = _s1(opener)
    md = md.replace("\n", "\r\n") if crlf else md
    assert unclosed(md)
    out = _refresh(tmp_path, md, **{_AC: "new AC", "System.Description": f"{opener}\nnew story"})
    # §2-§5 byte-identical (write_dod reads with universal newlines, so CRLF comes back as LF: pre-existing)
    assert out[out.index(SECTION2):] == md[md.index(SECTION2):].replace("\r\n", "\n")
    assert not unclosed(out) and "new AC" in out and "MY PLAN" in out and "see code" not in out
    assert ("&lt;!--" if opener == "<!--" else f"\\{opener}") in out  # the new §1 is escaped


def test_refresh_repairs_two_openers_in_section_1(tmp_path):
    md = _s1("```\n~~~")
    out = _refresh(tmp_path, md)
    assert out[out.index(SECTION2):] == md[md.index(SECTION2):] and "MY PLAN" in out


OPENERS = [("```", "~~~"), ("~~~", "```"), ("```", "<!--"), ("<!--", "```")]


@pytest.mark.parametrize("o1,o2", OPENERS)
def test_refresh_refuses_openers_in_section_1_and_2_before_a_marker_copy(tmp_path, o1, o2):
    _refused(tmp_path, _copy(_s1(o1), o2))


@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_refresh_refuses_an_opener_in_section_2_before_a_pasted_marker_copy(tmp_path, opener):
    _refused(tmp_path, _copy(render_dod(_wi(), "o", "p").replace("_(fill in)_", "MY PLAN", 1), opener))


@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_refresh_refuses_an_opener_in_section_4_with_a_marker_copy(tmp_path, opener):
    md = render_dod(_wi(), "o", "p").replace("_(none yet)_", f"{opener}\n{SECTION2}", 1)
    _refused(tmp_path, md)


@pytest.mark.parametrize("o1,o2", [("```", "~~~"), ("~~~", "```"), ("<!--", "```"), ("```", "<!--")])
def test_refresh_refuses_when_the_suffix_from_the_marker_is_still_unclosed(tmp_path, o1, o2):
    _refused(tmp_path, _s1(o1) + f"{o2}\ntrailing\n")  # §3-§5 headings are visible, but §5 is left open


# ---- P2-6: stack DoD items seed fresh ledgers only ----
import copy

from ado import done, stack
from ado.dod import stack_dod_items
from tests.conftest import FakeClient, approve, profile
from tests.test_stack_show import put

_FIVE = ["- [ ] every AC mapped to a passing real test", "- [ ] coverage meets the project's bar", "- [ ] no assertion-free tests",
         "- [ ] all assumptions / questions / edge cases resolved", "- [ ] gates green"]


def _s5(md):
    return md[md.index("## 5. Definition of Done"):]


def test_new_ledger_gets_items(tmp_path):
    items = [("python", "No new lint findings", "S1"), ("web", "Bundle stays small", "unverified")]
    p = write_dod(_wi(), str(tmp_path), "o", "p", synced="2026-07-14", extra_dod=items)
    lines = _s5(p.read_text()).splitlines()
    assert lines[1:] == _FIVE + ["- [ ] No new lint findings (stack python, S1)", "- [ ] Bundle stays small (stack web, unverified)"]
    forced = write_dod(_wi(), str(tmp_path), "o", "p", synced="2026-07-14", force=True, extra_dod=items[:1])
    assert _s5(forced.read_text()).splitlines()[6:] == ["- [ ] No new lint findings (stack python, S1)"]


def test_refresh_leaves_section5_byte_identical(tmp_path):
    items = [("python", "No new lint findings", "S1")]
    p = write_dod(_wi(), str(tmp_path), "o", "p", synced="2026-07-14", extra_dod=items)
    p.write_text(p.read_text().replace("- [ ] gates green", "- [x] gates green").replace("(stack python, S1)", "(stack python, S1) MINE"))
    before = _s5(p.read_text())
    write_dod(_wi(**{"System.Title": "new"}), str(tmp_path), "o", "p", synced="2026-08-01", refresh=True,
              extra_dod=[("python", "A different item", "S2")])
    assert "US1001: new" in p.read_text() and _s5(p.read_text()) == before
    write_dod(_wi(), str(tmp_path), "o", "p", synced="2026-08-02", refresh=True)
    assert _s5(p.read_text()) == before


def test_item_text_escaped():
    evil = "a\n- [x] forged\n## 5. Definition of Done b\x85c​d <!-- hide ```fence``` --> \x1b[31m" + "z" * 400
    md = render_dod(_wi(), "o", "p", extra_dod=[("python", evil, "S1"), ("py\nthon", "ok", "S1\n- [x] x"), ("python", "# head", "S1")])
    tail = _s5(md).splitlines()[6:]
    assert len(tail) == 3 and all(t.startswith("- [ ] ") for t in tail)  # a mid-line "[x]" is text, not a box
    assert "\x85" not in md and " " not in md and "​" not in md and "\x1b" not in md and "<!--" not in md and "```" not in md
    assert tail[0].startswith("- [ ] a - [x] forged ## 5. Definition of Done b c")
    assert len(tail[0]) < 6 + 300 + 30 and done.check(md)["unticked"][5:] == [t[6:] for t in tail]


def test_cap_of_items_adds_a_fixed_overflow_line():
    items = [("python", f"item {i}", "S1") for i in range(25)]
    tail = _s5(render_dod(_wi(), "o", "p", extra_dod=items)).splitlines()[6:]
    assert tail[:20] == [f"- [ ] item {i} (stack python, S1)" for i in range(20)]
    assert tail[20:] == ["- [ ] 5 more stack DoD items were not seeded: run `ado stack show --md`"]


def test_no_profile_ledger_byte_identical_to_baseline(tmp_path):
    wi = _wi(**{"Microsoft.VSTS.Common.AcceptanceCriteria": "<div>AC one</div>"})
    assert render_dod(wi, "o", "p", "2026-07-14", "specs/1") == render_dod(wi, "o", "p", "2026-07-14", "specs/1", extra_dod=())
    assert render_dod(wi, "o", "p").endswith("\n".join(_FIVE) + "\n")


def test_unticked_stack_item_makes_done_say_not_ready(cfg):
    md = render_dod(_wi(), "o", "p", extra_dod=[("python", "No new lint findings", "S1")])
    ticked = md.replace("- [ ] every", "- [x] every").replace("- [ ] coverage", "- [x] coverage").replace("- [ ] no assertion", "- [x] no assertion") \
        .replace("- [ ] all assumptions", "- [x] all assumptions").replace("- [ ] gates", "- [x] gates")
    chk = done.check(ticked)
    assert chk == {"ready": False, "unticked": ["No new lint findings (stack python, S1)"]}
    assert "No new lint findings (stack python, S1)" in done.render_plan(1001, chk, cfg) and "Not ready" in done.render_plan(1001, chk, cfg)
    assert done.check(ticked.replace("- [ ] No new", "- [x] No new"))["ready"] is True


def test_stack_dod_items_reads_the_profile_in_stack_order():
    p = profile()
    p["stacks"].append({"id": "web", "root": "web", "dod": [{"item": "Docs updated", "source": "unverified"}]})
    assert stack_dod_items(p) == [("python", "No new lint findings", "S1"), ("web", "Docs updated", "unverified")]
    assert stack_dod_items(None) == []


def _cli(cfg, tmp_path, monkeypatch, refresh=False, force=False):
    from ado.__main__ import dispatch
    monkeypatch.chdir(tmp_path)
    client = FakeClient(cfg, responses=[{"id": 1001, "fields": {"System.Title": "T"}}])
    client.config.dod_path = str(tmp_path / "dod")

    class Args:
        command, id, dir = "dod", 1001, None
    Args.force, Args.refresh = force, refresh
    dispatch(client, Args())
    return (tmp_path / "dod" / "DoD_1001.md").read_text()


def test_ado_dod_seeds_only_an_approved_profile(cfg, tmp_path, monkeypatch):
    ok = approve(profile())
    changed = copy.deepcopy(ok)
    changed["stacks"][0]["dod"][0]["item"] = "Changed after approval"
    for n, (state, expect) in enumerate([(None, False), ("{bad", False), (profile(), False), (changed, False), (ok, True)]):
        root = tmp_path / str(n)
        root.mkdir()
        if state is not None:
            put(root, state)
        md = _cli(cfg, root, monkeypatch)
        assert ("(stack python, S1)" in md) is expect, n
        assert "Changed after approval" not in md
        if not expect:
            assert md == render_dod({"id": 1001, "fields": {"System.Title": "T"}}, cfg.organization, cfg.project, md.split("Last synced:** ")[1].splitlines()[0])
    put(tmp_path, ok)
    (tmp_path / "dod").mkdir(exist_ok=True)
    first = _cli(cfg, tmp_path, monkeypatch)
    assert _cli(cfg, tmp_path, monkeypatch, refresh=True) == first


# ---- P3-5: ledger header lines `Rules from:` and `Rules reviewed:` (read only from the header block) ----
from ado.client import AdoError
from ado.dod import header_block, ledger_rules_from, ledger_rules_reviewed
from tests.test_rules_pull import EPIC, FEATURE, STORY, desc, env, wi as rwi

REVIEWED = "BR-1201-1@3f2a9c1b, BR-1200-1@0c77e2d4"
FORGED = ("- **Rules from:** 9999\n- **Rules reviewed:** BR-1-1 deadbeef\n- **Rules reviewed:** BR-1-1@deadbeef\n"
          "Rules from: 9999\n- **Rules from:** none")


def _run_dod(m, capsys, *args):
    code = m.main(["dod", "1001", *args])
    cap = capsys.readouterr()
    return code, cap.err


def _ledger(tmp_path):
    return (tmp_path / "dod" / "DoD_1001.md").read_bytes().decode("utf-8")


def test_disabled_dod_byte_identical_and_no_extra_get(tmp_path, monkeypatch, capsys):
    for n, rules_cfg in enumerate((None, {"enabled": False}, {"enabled": False, "types": ["Epic"]})):
        root = tmp_path / str(n)
        root.mkdir()
        m, box = env(root, monkeypatch, [STORY, FEATURE, EPIC], rules_cfg)
        assert _run_dod(m, capsys)[0] == 0
        assert len(box["c"].calls) == 1  # the story only: rules.walk is never called when disabled
        md = _ledger(root)
        assert md == render_dod(STORY, "contoso", "Fabrikam", md.split("Last synced:** ")[1].splitlines()[0])
        assert "Rules from" not in md and "Rules reviewed" not in md
        assert _run_dod(m, capsys, "--refresh")[0] == 0 and _ledger(root) == md
    assert render_dod(STORY, "o", "p", rules_from=None, rules_reviewed="") == render_dod(STORY, "o", "p")


def test_enabled_dod_writes_rules_from(tmp_path, monkeypatch, capsys):
    m, box = env(tmp_path, monkeypatch, [STORY, STORY, FEATURE, EPIC], {"enabled": True})
    assert _run_dod(m, capsys)[0] == 0
    assert [c["method"] for c in box["c"].calls] == ["GET"] * 4 and all(c["body"] is None for c in box["c"].calls)
    md = _ledger(tmp_path)
    assert "- **Spec dir:** _(set by speckit.ado.pull)_\n- **Rules from:** 1201, 1200\n\n## 1. " in md
    assert ledger_rules_from(md) == {"present": True, "valid": True, "ids": [1201, 1200]}
    assert ledger_rules_reviewed(md) == {"present": False, "valid": True, "hashes": {}}
    # no ancestor with a section: `none`
    (tmp_path / "x").mkdir()
    m, _ = env(tmp_path / "x", monkeypatch,
               [STORY, STORY, rwi(1201, "Feature", "<p>no rules</p>")], {"enabled": True})
    assert _run_dod(m, capsys)[0] == 0
    assert ledger_rules_from(_ledger(tmp_path / "x")) == {"present": True, "valid": True, "ids": []}
    assert "- **Rules from:** none\n" in _ledger(tmp_path / "x")


def _edit_header(tmp_path, line):
    p = tmp_path / "dod" / "DoD_1001.md"
    p.write_bytes(p.read_bytes().replace(b"\n\n## 1. ", f"\n{line}\n\n## 1. ".encode(), 1))


def test_refresh_recomputes_rules_from_and_keeps_rules_reviewed(tmp_path, monkeypatch, capsys):
    m, _ = env(tmp_path, monkeypatch, [STORY, STORY, FEATURE, EPIC, STORY, STORY, FEATURE, EPIC], {"enabled": True})
    assert _run_dod(m, capsys)[0] == 0
    _edit_header(tmp_path, f"- **Rules reviewed:** {REVIEWED}")
    p = tmp_path / "dod" / "DoD_1001.md"
    p.write_bytes(p.read_bytes().replace(b"- **Rules from:** 1201, 1200", b"- **Rules from:** 7"))  # a stale value
    p.write_bytes(p.read_bytes().replace(b"_(fill in)_", b"MY REAL PLAN", 1))
    assert _run_dod(m, capsys, "--refresh")[0] == 0
    md = _ledger(tmp_path)
    assert ledger_rules_from(md)["ids"] == [1201, 1200] and "MY REAL PLAN" in md
    assert f"\n- **Rules reviewed:** {REVIEWED}\n" in md and md.count("Rules reviewed") == 1
    # the walk changed (the Epic is gone): recomputed, not kept
    m, _ = env(tmp_path, monkeypatch, [STORY, STORY, rwi(1201, "Feature", desc((1, "approved")))], {"enabled": True})
    assert _run_dod(m, capsys, "--refresh")[0] == 0
    md2 = _ledger(tmp_path)
    assert ledger_rules_from(md2)["ids"] == [1201] and f"\n- **Rules reviewed:** {REVIEWED}\n" in md2
    # rules switched off: no Rules from line (baseline), Rules reviewed still kept byte for byte, no walk GET
    m, box = env(tmp_path, monkeypatch, [STORY], {"enabled": False})
    assert _run_dod(m, capsys, "--refresh")[0] == 0 and len(box["c"].calls) == 1
    md3 = _ledger(tmp_path)
    assert "Rules from" not in md3 and f"\n- **Rules reviewed:** {REVIEWED}\n" in md3
    assert md3.replace(f"- **Rules reviewed:** {REVIEWED}\n", "") == render_dod(STORY, "contoso", "Fabrikam", md3.split("Last synced:** ")[1].splitlines()[0]).replace(
        "_(fill in)_", "MY REAL PLAN", 1)


@pytest.mark.parametrize("bad", ["BR-1201-1@3f2a9c1b,BR-1200-1@0c77e2d4", "BR-1201-1@3f2a9c1b, BR-1201-1@0c77e2d4", "BR-1201-1", "BR-1-1@deadbeef0",
                                 "br-1201-1@3f2a9c1b", "BR-1201-01@3f2a9c1b", "BR-../1-1@3f2a9c1b", ""])
def test_refresh_refuses_an_invalid_rules_reviewed_line(tmp_path, bad):
    md = render_dod(_wi(), "o", "p").replace("\n\n## 1. ", f"\n- **Rules reviewed:** {bad}\n\n## 1. ", 1)
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    with pytest.raises(DodExists, match="Rules reviewed"):
        write_dod(_wi(), tmp_path, "o", "p", refresh=True)
    assert p.read_bytes().decode("utf-8") == md


REVIEWED_LINES = [f"- **Rules reviewed:** {REVIEWED}", "- **Rules reviewed:** none", "- **Rules reviewed:** BR-1201-1", "- **Rules reviewed:** ",
                  "- **Rules reviewed:** BR-1201-1@3f2a9c1b\n- **Rules reviewed:** BR-1200-1@0c77e2d4"]  # valid, none, malformed, empty, repeated


@pytest.mark.parametrize("line", REVIEWED_LINES)
def test_force_refuses_with_reviewed_line_and_leaves_ledger_untouched(tmp_path, monkeypatch, capsys, line):
    m, _ = env(tmp_path, monkeypatch, [STORY], {"enabled": False})
    (tmp_path / "dod").mkdir()
    old = render_dod(STORY, "contoso", "Fabrikam", "old").replace("\n\n## 1. ", f"\n{line}\n\n## 1. ", 1)
    p = tmp_path / "dod" / "DoD_1001.md"
    p.write_bytes(old.encode("utf-8"))
    code, err = _run_dod(m, capsys, "--force")
    assert code == 2 and "refusing to --force" in err and "ado rules <id> --confirm" in err and "Rules reviewed" in err
    assert _ledger(tmp_path) == old and list((tmp_path / "dod").iterdir()) == [p]  # untouched, no temp file left
    assert "The branch history belongs to the PR author: a squash or rebase before pushing erases this trace." in err
    with pytest.raises(DodExists, match="refusing to --force"):
        write_dod(STORY, tmp_path / "dod", "contoso", "Fabrikam", force=True)
    assert _run_dod(m, capsys, "--refresh")[0] in (0, 2)  # --refresh is the way that keeps the line (a malformed one is refused, as before)


def test_force_refuses_a_reviewed_line_in_an_untrusted_header_and_hostile_bytes(tmp_path):
    p = tmp_path / "DoD_1001.md"
    for old in (b"# t <!--\n- **Rules reviewed:** BR-1201-1@3f2a9c1b\n\n## 1. x\n", b"# t \xff\xfe\n\n- **Rules reviewed:** BR-1201-1@3f2a9c1b\n\n## 1. x\n"):
        p.write_bytes(old)
        with pytest.raises(DodExists, match="refusing to --force"):
            write_dod(_wi(), tmp_path, "o", "p", force=True)
        assert p.read_bytes() == old


def test_advice_never_says_use_force_without_the_reviewed_line_caveat(tmp_path):
    """Every message that points to --force says it refuses while a `Rules reviewed:` line exists."""
    p = tmp_path / "DoD_1001.md"
    base = render_dod(_wi(), "o", "p")
    p.write_bytes(base.encode("utf-8"))
    with pytest.raises(DodExists) as e:
        write_dod(_wi(), tmp_path, "o", "p")
    texts = [str(e.value)]
    p.write_bytes(base.replace("## 2. Brainstorm", "<!-- ## 2. Brainstorm", 1).encode("utf-8"))  # unclosed comment outside section 1
    with pytest.raises(DodExists) as e:
        write_dod(_wi(), tmp_path, "o", "p", refresh=True)
    texts.append(str(e.value))
    p.write_bytes(base.replace("## 2. Brainstorm & plan", "## 2. other", 1).encode("utf-8"))  # no section 2 marker
    with pytest.raises(DodExists) as e:
        write_dod(_wi(), tmp_path, "o", "p", refresh=True)
    texts.append(str(e.value))
    for t in texts:
        assert "--force" in t and "Rules reviewed" in t and "removal is traced" in t, t
    readme = " ".join(open("README.md", encoding="utf-8").read().split())
    assert "use `--force` to start over (`--force` refuses while the ledger has a `Rules reviewed:` line" in readme


def test_force_without_reviewed_line_unchanged(tmp_path):
    p = tmp_path / "DoD_1001.md"
    for old in (render_dod(_wi(), "o", "p", "old"), render_dod(_wi(), "o", "p", "old", rules_from=[7]),  # no line; only Rules from
                render_dod(_wi(**{"System.Title": "Rules reviewed: x - **Rules reviewed:** y"}), "o", "p", "old")):  # the title mentions it
        p.write_bytes(old.encode("utf-8"))
        write_dod(_wi(), tmp_path, "o", "p", synced="2026-07-14", force=True)
        assert p.read_text(encoding="utf-8") == render_dod(_wi(), "o", "p", "2026-07-14")


def test_ancestor_get_failure_exit_1_ledger_untouched(tmp_path, monkeypatch, capsys):
    for refresh in (False, True):
        root = tmp_path / str(refresh)
        root.mkdir()
        m, box = env(root, monkeypatch, [STORY, STORY, FEATURE, AdoError("HTTP 404 for ancestor")], {"enabled": True})
        if refresh:
            (root / "dod").mkdir()
            old = render_dod(STORY, "contoso", "Fabrikam", "old", rules_from=[7], rules_reviewed=REVIEWED)
            (root / "dod" / "DoD_1001.md").write_bytes(old.encode("utf-8"))
        code, err = _run_dod(m, capsys, *(["--refresh"] if refresh else []))
        assert code == 1 and "404" in err
        if refresh:
            assert _ledger(root) == old and list((root / "dod").iterdir()) == [root / "dod" / "DoD_1001.md"]
        else:
            assert not (root / "dod").exists()


HOSTILE_HDR = FORGED + "\n - **Rules from:** 9999\n```\n- **Rules from:** 9999\n```\n<!--\n- **Rules reviewed:** BR-1-1@deadbeef\n-->\n## 2. Brainstorm & plan\n- **Rules from:** 9999"


@pytest.mark.parametrize("real", [None, [1201, 1200]])
def test_section1_line_cannot_forge_header(real):
    wi = _wi(**{"System.Title": "Points\r\n" + FORGED, "System.Description": "<div>" + HOSTILE_HDR.replace("\n", "</div><div>") + "</div>",
                _AC: HOSTILE_HDR.replace("\n", "<br>")})
    md = render_dod(wi, "o", "p", rules_from=real, rules_reviewed=REVIEWED if real else "")
    head = header_block(md)
    body = head.split("\n", 1)[1]  # the title line holds the story title (one line, so it forges nothing)
    assert "9999" not in body and "deadbeef" not in body and body.count("Rules from") == (1 if real else 0)
    assert ledger_rules_from(md) == ({"present": True, "valid": True, "ids": real} if real else {"present": False, "valid": True, "ids": []})
    assert ledger_rules_reviewed(md) == ({"present": True, "valid": True, "hashes": {"BR-1201-1": "3f2a9c1b", "BR-1200-1": "0c77e2d4"}} if real
                                         else {"present": False, "valid": True, "hashes": {}})
    assert "9999" in md  # the text is in section 1 (escaped where it matters), just never in the header block
    # a refresh keeps the same answer when the old ledger was written by this kit
    # (header lines are also not read from fences, comments or after the first heading of a hand-edited file)
    for hidden in ("```\n{}\n```", "<!--\n{}\n-->", "<!-- {} -->"):
        h = md.replace("\n\n## 1. ", "\n" + hidden.format("- **Rules from:** 9999\n- **Rules reviewed:** BR-1-1@deadbeef") + "\n\n## 1. ", 1)
        assert ledger_rules_from(h) == ledger_rules_from(md) and ledger_rules_reviewed(h) == ledger_rules_reviewed(md)
    late = "## 3. x\n- **Rules from:** 9999\n- **Rules reviewed:** BR-1-1@deadbeef\n"
    assert ledger_rules_from(md + late) == ledger_rules_from(md) and ledger_rules_reviewed(md + late) == ledger_rules_reviewed(md)


def test_a_title_naming_the_labels_is_not_a_header_line():
    md = render_dod(_wi(**{"System.Title": "Rules from: marketing - **Rules from:** 5 Rules reviewed:"}), "o", "p")
    assert ledger_rules_from(md)["present"] is False and ledger_rules_reviewed(md)["present"] is False


def _hdr(*lines, crlf=False):
    md = "# DoD — US1001: T\n\n- **ADO:** u\n" + "".join(l + "\n" for l in lines) + "\n## 1. Seeded\n\n- **Rules from:** 9999\n"
    return md.replace("\n", "\r\n") if crlf else md


@pytest.mark.parametrize("value", ["../1", "1e3", "1234567890", "1201, 1200, 1100, 1000", "1201, 1201", "1201,1200", "", "none, 1201", "0", "007", "-1",
                                   "1201 1200", "１２０１", "1201,", "None", "1201 ", " 1201"])
def test_invalid_rules_from(value):
    out = ledger_rules_from(_hdr(f"- **Rules from:** {value}"))
    assert out == {"present": True, "valid": False, "ids": []}, value


def test_invalid_rules_from_forms():
    bad = {"present": True, "valid": False, "ids": []}
    assert ledger_rules_from(_hdr("- **Rules from:** 1201", "- **Rules from:** 1200")) == bad  # two lines
    assert ledger_rules_from(_hdr("- **Rules from:** 1201", "- **Rules from:** 1201")) == bad
    assert ledger_rules_from(_hdr("- **Rules from:**1201")) == bad
    assert ledger_rules_from(_hdr("- **Rules from**: 1201")) == bad
    assert ledger_rules_from(_hdr("* **rules from:** 1201")) == bad
    assert ledger_rules_from(_hdr("- **Rules from:** 1201\u2028- **Rules from:** 1200")) == bad  # a line break the file's readers also see
    assert ledger_rules_from(_hdr("<!-- never closed", "- **Rules from:** 1201")) == bad  # an unclosed opener hides the rest of the header
    assert ledger_rules_from(_hdr("- **Rules from:** 1201", "- **Rules from:** none")) == bad
    ok = ledger_rules_from(_hdr("- **Rules from:** 999999999, 1, 2"))
    assert ok == {"present": True, "valid": True, "ids": [999999999, 1, 2]}
    assert ledger_rules_from(_hdr("- **Rules from:** none")) == {"present": True, "valid": True, "ids": []}
    assert ledger_rules_from(_hdr("- **Spec dir:** Rules from: 1201")) == {"present": False, "valid": True, "ids": []}
    bad = {"present": True, "valid": False, "hashes": {}}
    for line in ("- **Rules reviewed:** BR-1201-1@3f2a9c1b, BR-1201-1@3f2a9c1b", "- **Rules reviewed:** BR-1201-1@3F2A9C1B", "- **Rules reviewed:** BR-1201-1@3f2a9c1",
                 "- **Rules reviewed:** BR-1201-1@3f2a9c1b ", "- **Rules reviewed:** BR-0-1@3f2a9c1b", "- **Rules reviewed:** BR-1201-0@3f2a9c1b",
                 "- **Rules reviewed:** BR-1201-10000@3f2a9c1b", "- **Rules reviewed:** x BR-1201-1@3f2a9c1b", "- **Rules reviewed:** BR-1201-1 deadbeef"):
        assert ledger_rules_reviewed(_hdr(line)) == bad, line
    assert ledger_rules_reviewed(_hdr("- **Rules reviewed:** BR-1201-1@3f2a9c1b", "- **Rules reviewed:** BR-1200-1@3f2a9c1b")) == bad
    assert ledger_rules_reviewed(_hdr("- **Rules reviewed:** none")) == {"present": True, "valid": True, "hashes": {}}


def test_crlf_ledger_header_values_parse():
    md = _hdr("- **Rules from:** 1201, 1200", f"- **Rules reviewed:** {REVIEWED}", crlf=True)
    assert "\r\n" in md and ledger_rules_from(md) == {"present": True, "valid": True, "ids": [1201, 1200]}
    assert ledger_rules_reviewed(md) == {"present": True, "valid": True, "hashes": {"BR-1201-1": "3f2a9c1b", "BR-1200-1": "0c77e2d4"}}
    assert "9999" not in header_block(md) and "\r" not in header_block(md)
    lone = render_dod(_wi(), "o", "p", rules_from=[1201], rules_reviewed=REVIEWED).replace("\n", "\r")  # old-Mac line ends
    assert ledger_rules_from(lone)["ids"] == [1201] and ledger_rules_reviewed(lone)["valid"] is True


def test_render_validates_what_it_writes():
    for bad in ([1, 1], [1, 2, 3, 4], [0], [10 ** 9], ["1201"], [True], [1.0], 5):
        with pytest.raises(ValueError, match="rules_from"):
            render_dod(_wi(), "o", "p", rules_from=bad)
    for bad in ("BR-1201-1", "BR-1201-1@3f2a9c1b\n- **Rules from:** 9", "x", " BR-1201-1@3f2a9c1b"):
        with pytest.raises(ValueError, match="rules_reviewed"):
            render_dod(_wi(), "o", "p", rules_from=[1], rules_reviewed=bad)
    assert "- **Rules from:** none\n" in render_dod(_wi(), "o", "p", rules_from=[])
    assert header_block(render_dod(_wi(), "o", "p", rules_from=[5, 4], rules_reviewed=REVIEWED)).endswith(
        f"- **Rules from:** 5, 4\n- **Rules reviewed:** {REVIEWED}\n\n")


def test_refresh_still_repairs_a_legacy_ledger_whose_title_left_a_comment_open(tmp_path):
    from ado.dod import unclosed
    md = render_dod(_wi(), "o", "p").replace("# DoD — US1001: T", "# DoD — US1001: T <!-- legacy", 1)  # old ledgers did not escape the title
    assert unclosed(md)
    out = _refresh(tmp_path, md)  # no Rules reviewed line can be read from an open header: rebuilt as before, not refused
    assert not unclosed(out) and "legacy" not in out and "Rules reviewed" not in out


def test_refresh_of_a_crlf_ledger_keeps_rules_reviewed(tmp_path):
    md = render_dod(_wi(), "o", "p", rules_from=[7], rules_reviewed=REVIEWED).replace("\n", "\r\n")
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    write_dod(_wi(), tmp_path, "o", "p", refresh=True, rules_from=[1201])
    out = p.read_bytes().decode("utf-8")
    assert ledger_rules_reviewed(out)["hashes"] == {"BR-1201-1": "3f2a9c1b", "BR-1200-1": "0c77e2d4"} and ledger_rules_from(out)["ids"] == [1201]
    assert f"\n- **Rules reviewed:** {REVIEWED}\n" in out  # (write_dod reads with universal newlines: the file comes back with LF, as before)


# ---- P3-5b: header values are escaped; an untrusted header fails closed ----
_FORGE_DESC = "<p>--&gt;</p><p>- **Rules from:** 9999</p><p>- **Rules reviewed:** BR-1-1@deadbeef</p>"
_REAL_FROM = {"present": True, "valid": True, "ids": [1201]}
_REAL_REV = {"present": True, "valid": True, "hashes": {"BR-1201-1": "3f2a9c1b"}}


def _header_untrusted(md):
    return not ledger_rules_from(md)["valid"]  # an untrusted header reads as present and invalid, even with no Rules line


@pytest.mark.parametrize("real", [False, True])
@pytest.mark.parametrize("where", ["System.State", "System.WorkItemType", "spec_dir", "organization", "synced"])
def test_an_opener_in_a_header_value_cannot_forge_the_header(where, real):
    """`<!--` in a header value plus `-->` in the Description used to hide the real lines and let the Description stand in for them."""
    opener = "x <!--"
    wi = _wi(**{"System.Description": _FORGE_DESC, **({where: opener} if where.startswith("System.") else {})})
    kw = {"spec_dir": opener} if where == "spec_dir" else {"synced": opener} if where == "synced" else {}
    md = render_dod(wi, "o <!--" if where == "organization" else "o", "p", rules_from=[1201] if real else None,
                    rules_reviewed="BR-1201-1@3f2a9c1b" if real else "", **kw)
    assert not _header_untrusted(md)
    assert "9999" not in header_block(md) and "deadbeef" not in header_block(md)
    assert ledger_rules_from(md) == (_REAL_FROM if real else {"present": False, "valid": True, "ids": []})
    assert ledger_rules_reviewed(md) == (_REAL_REV if real else {"present": False, "valid": True, "hashes": {}})
    assert "&lt;!--" in header_block(md)


def test_header_values_with_a_line_break_stay_on_one_line():
    md = render_dod(_wi(**{"System.State": "Active\n## x - **Rules from:** 9999", "System.WorkItemType": "Bug\r\n- **Rules from:** 9999"}),
                    "o", "p", spec_dir="a - **Rules from:** 9999", rules_from=[1201])
    assert header_block(md).count("\n") == len(header_block(md).splitlines()) and ledger_rules_from(md) == _REAL_FROM
    assert "- **Type / State:** Bug - **Rules from:** 9999 / Active ## x - **Rules from:** 9999\n" in md


def test_header_values_without_special_characters_are_unchanged():
    md = render_dod(_wi(**{"System.State": "In Progress", "System.WorkItemType": "User Story"}), "contoso", "Fabrikam", "2026-07-14", "specs/001-loyalty")
    assert header_block(md) == (
        "# DoD — US1001: T\n\n- **ADO:** https://dev.azure.com/contoso/Fabrikam/_workitems/edit/1001\n- **Type / State:** User Story / In Progress\n"
        f"- **Last synced:** 2026-07-14\n- **AC hash:** {ac_hash(_wi())}\n- **Spec dir:** specs/001-loyalty\n\n")


_LEGACY_DESC = "<p>--&gt;</p><p>- **Rules reviewed:** BR-1-1@deadbeef</p>"


def _legacy():
    """A ledger from before 0.2.0: the title opened `<!--` (not escaped yet) and the Description closes it."""
    wi = _wi(**{"System.Description": _LEGACY_DESC})
    return wi, render_dod(wi, "o", "p").replace("# DoD — US1001: T", "# DoD — US1001: T <!--", 1)


def test_a_comment_opened_in_the_title_and_closed_in_the_description_makes_the_header_untrusted():
    _, md = _legacy()
    assert ledger_rules_reviewed(md) == {"present": True, "valid": False, "hashes": {}}
    assert ledger_rules_from(md) == {"present": True, "valid": False, "ids": []}


def test_refresh_refuses_to_launder_a_forged_rules_reviewed_through_an_untrusted_header(tmp_path):
    wi, md = _legacy()
    p = tmp_path / "DoD_1001.md"
    p.write_bytes(md.encode("utf-8"))
    with pytest.raises(DodExists, match="Rules reviewed"):
        write_dod(wi, tmp_path, "o", "p", refresh=True)
    assert p.read_bytes().decode("utf-8") == md and list(tmp_path.iterdir()) == [p]


def test_cli_refresh_of_a_laundering_ledger_exits_2_with_the_ledger_untouched(tmp_path, monkeypatch, capsys):
    _, md = _legacy()
    m, _b = env(tmp_path, monkeypatch, [rwi(1001, "User Story", _LEGACY_DESC)], {"enabled": False})
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_1001.md").write_bytes(md.encode("utf-8"))
    code, err = _run_dod(m, capsys, "--refresh")
    assert code == 2 and "Rules reviewed" in err and "deadbeef" not in err
    assert _ledger(tmp_path) == md


HIDDEN_FIRST_H = ["<!--\n## 1. S\n-->", "```\n## 1. S\n```", "~~~\n## 1. S\n~~~", "<!--\n## 1. S"]


@pytest.mark.parametrize("hide", HIDDEN_FIRST_H)
def test_a_first_heading_hidden_by_a_comment_or_fence_makes_the_header_untrusted(hide):
    md = f"# DoD — US1001: T\n\n- **ADO:** u\n- **Rules from:** 1201\n- **Rules reviewed:** {REVIEWED}\n{hide}\n## 2. Brainstorm & plan\n"
    assert ledger_rules_from(md) == {"present": True, "valid": False, "ids": []}
    assert ledger_rules_reviewed(md) == {"present": True, "valid": False, "hashes": {}}
    ok = md.replace(hide + "\n", "## 1. S\n" + hide + "\n", 1)  # a visible first heading: trusted again
    assert ledger_rules_from(ok)["ids"] == [1201] and ledger_rules_reviewed(ok)["valid"] is True


@pytest.mark.parametrize("prefix", ["> ", ">", "> > ", ">   "])
@pytest.mark.parametrize("line,reader", [("- **Rules from:** 1201", ledger_rules_from), (f"- **Rules reviewed:** {REVIEWED}", ledger_rules_reviewed)])
def test_a_blockquote_prefixed_header_line_is_invalid_not_absent(prefix, line, reader):
    out = reader(_hdr(prefix + line))
    assert out["present"] is True and out["valid"] is False


@pytest.mark.parametrize("given,recorded", [("specs/my feature", "specs/my feature"), ("specs/café", "specs/café"), ("specs/a&b", "specs/a&b"),
                                            ("specs/a <!--x", "specs/a &lt;!--x"), ("#odd", "\\#odd")])
def test_spec_dir_round_trips_and_is_stable_across_refreshes(tmp_path, given, recorded):
    p = write_dod(_wi(), tmp_path, "o", "p", synced="2026-07-14", spec_dir=given)
    first = p.read_text(encoding="utf-8")
    assert ledger_spec_dir(first) == recorded
    for _ in range(2):  # a refresh without --spec-dir re-reads the recorded value and writes it back unchanged
        write_dod(_wi(), tmp_path, "o", "p", synced="2026-07-14", refresh=True)
        assert p.read_text(encoding="utf-8") == first and ledger_spec_dir(first) == recorded
    assert "<!--" not in first and (recorded == given or ledger_spec_dir(first) != given)  # an escaped one is not the given path: fails closed
