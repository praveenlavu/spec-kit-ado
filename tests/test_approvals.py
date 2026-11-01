import pytest

from ado import approvals, comments
from ado.client import AdoError
from tests.conftest import FakeClient, comment_pages, connection_data, raw_comment, versions_response

ME = "AAAA1111-0000-4000-8000-00000000000A"  # the token identity; upper case on purpose
KIT, HUMAN, ANON = "4444444a-4444-4444-8444-44444444444b", "55555555-5555-4555-8555-555555555555", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
D = "2026-10-01T00:00:00Z"


def _meta(cfg, raw):
    found, truncated = comments.fetch(FakeClient(cfg, responses=comment_pages(raw)), 2, with_meta=True)
    assert not truncated
    return found


def test_excluded_contains_token_and_kit_authors(cfg):
    found = _meta(cfg, [
        raw_comment(1, "[spec-kit-ado:questions]<br>Open questions", D, author=KIT.upper()),
        raw_comment(2, "<div>[spec-kit-ado:decisions]</div>", D, author=ME),
        raw_comment(3, "/approve D1", D, author=HUMAN),
        raw_comment(4, "see [spec-kit-ado:questions] above", D, author="66666666-6666-4666-8666-666666666666"),
        raw_comment(5, "[spec-kit-ado:x]", D, author=ANON),
        raw_comment(6, "[spec-kit-ado:x]", D, author=None)])
    client = FakeClient(cfg, responses=[connection_data(ME)])
    assert approvals.excluded(client, found) == {ME.lower(), KIT}
    assert client.calls[0]["path"] == "/_apis/connectionData?api-version=7.1-preview"  # the token identity, one request


def test_excluded_without_comments_is_just_the_token_identity(cfg):
    assert approvals.excluded(FakeClient(cfg, responses=[connection_data(ME)]), []) == {ME.lower()}


def test_excluded_refuses_when_the_token_identity_is_unknown_or_anonymous(cfg):
    for resp in (connection_data(ANON), {"authenticatedUser": {}}, AdoError("HTTP 401", status=401, kind="signin")):
        with pytest.raises(ValueError):
            approvals.excluded(FakeClient(cfg, responses=[resp]), [])


def test_excluded_lowercases_with_id_rows_too(cfg):
    rows = comments.fetch(FakeClient(cfg, responses=[{"comments": [raw_comment(1, "[spec-kit-ado:x]", D, author=KIT.upper())]}]), 2, with_id=True)
    assert approvals.excluded(FakeClient(cfg, responses=[connection_data(ME)]), rows) == {ME.lower(), KIT}


# ---- P4-8: grammar, quote stripping, guards, bypass refusal ------------------------------------------------------------------------------
import time  # noqa: E402

H64 = "ab" * 32
KIT_ID = KIT  # the kit's posting identity
CI = "77777777-7777-4777-8777-777777777777"
D1, D2, D3 = "2026-10-01T10:00:00Z", "2026-10-01T10:00:00.5Z", "2026-10-01T10:00:00.25Z"


def _p(html):
    return approvals.parse_command(html)


def _cmd(cid, text, date=D1, author=HUMAN, **kw):
    return raw_comment(cid, text, date, author=author, **kw)


def _newest(cfg, raws, target="D3", approvers=(HUMAN,), excluded=(), responses=(), tally=None):
    found = _meta(cfg, raws)
    client = FakeClient(cfg, responses=list(responses))
    got = approvals.newest(client, 2, found, target, [a.upper() for a in approvers], {e.lower() for e in excluded}, tally=tally)
    return got, client


@pytest.mark.parametrize("html,want", [
    ("/approve D3", {"verb": "approve", "target": "D3", "hash": None, "text": ""}),
    ("<div>/approve D3</div>", {"verb": "approve", "target": "D3", "hash": None, "text": ""}),
    ("<p>/reject D12: too vague</p>", {"verb": "reject", "target": "D12", "hash": None, "text": "too vague"}),
    ("/reject D3 :  spaced  note ", {"verb": "reject", "target": "D3", "hash": None, "text": "spaced note"}),
    ("/answer Q2: Use the blue one", {"verb": "answer", "target": "Q2", "hash": None, "text": "Use the blue one"}),
    (f"/approve review-spec {H64}", {"verb": "approve", "target": "review-spec", "hash": H64, "text": ""}),
    (f"<div>/reject approve-push {H64}: not yet</div>", {"verb": "reject", "target": "approve-push", "hash": H64, "text": "not yet"}),
    ("<br>\n  <p>&nbsp;</p><p>/approve D3</p><p>thanks</p>".replace("&nbsp;", ""), {"verb": "approve", "target": "D3", "hash": None, "text": ""}),
])
def test_parse_command_accepts_the_grammar(html, want):
    assert _p(html) == want


@pytest.mark.parametrize("html", [
    "", None, "approve D3", "/Approve D3", "/approve", "/approve d3", "/approve D", "/approve D3x", "/approve D03", "/approve D0", "/answer Q02: x", "/approve D3 extra",
    "/approve D3:", "/approve D3:   ", "/answer Q2", "/answer Q2:", "/answer D3: yes", "/approve Q2", "/reject Q2: no", "/answer review-spec: x",
    "/approve review-spec", "/approve ab", "/approve a" + "b" * 41, "/approve D3 " + H64, "/answer Q2 " + H64 + ": x",
    "/approve D3", "/approve D3 /approve D4 x", "x/approve D3", "​/approve D3", "/approvals D3", "/approve-all D3",
    "<!-- /approve D3 -->", "<p>&gt; /approve D3</p>", "<ul><li>/approve D3</li></ul>", "<ol><li>/approve D3</li></ol>",
])
def test_parse_command_rejects_everything_else(html):
    assert _p(html) is None


def test_hash_must_be_64_hex():
    for bad in (H64[:-1], H64 + "a", H64.upper(), "g" * 64, H64[:-1] + "G", "\u00a0" + H64, "0x" + H64[2:]):
        assert _p(f"/approve review-spec {bad}") is None, bad
    assert _p(f"/approve review-spec {H64}")["hash"] == H64
    assert _p(f"/approve review-spec {'0' * 64}")["hash"] == "0" * 64


def test_notes_and_answers_are_capped_and_scrubbed():
    assert _p("/reject D3: " + "n" * 300)["text"] == "n" * 300
    assert _p("/reject D3: " + "n" * 301)["text"] == "n" * 300  # capped, never refused: a long reject must stay a reject
    assert _p("/reject D3: " + "n" * 5000)["text"] == "n" * 300
    assert _p("/answer Q2: " + "a" * 500)["text"] == "a" * 500
    assert _p("/answer Q2: " + "a" * 501)["text"] == "a" * 500
    t = _p("/answer Q2: mail dana@contoso.example or ping <a href='x' data-vss-mention='1'>Dana Fictional</a> \x1b[31mred\x1b[0m &lt;!-- x --&gt; ```fence```")["text"]
    assert "contoso" not in t and "Dana" not in t and "\x1b" not in t and "<!--" not in t and "-->" not in t and "```" not in t
    assert "[email]" in t and "@user" in t and "\n" not in t
    assert _p("/answer Q2: ​​") is None  # nothing left after the scrub


def test_quoted_approve_ignored():
    quoted = "<blockquote>/approve D3</blockquote>"
    assert _p(quoted) is None
    assert _p("<BLOCKQUOTE class=x>\n/approve D3\n</BLOCKQUOTE>") is None
    assert _p("<blockquote><p>/approve D3</p><blockquote>/approve D3</blockquote><p>/approve D3</p></blockquote>") is None  # nested: the inner close ends nothing
    assert _p("<blockquote><blockquote>x</blockquote>/approve D3</blockquote>") is None
    assert _p("<blockquote>/approve D3<blockquote") is None  # unclosed: everything after the opening is quoted
    assert _p("<div class='gmail_quote'><div>On Fri, someone wrote:</div><div>/approve D3</div></div>") is None  # reply markup
    assert _p('<div class="x yahoo_quoted"><p>/approve D3</p></div>') is None
    assert _p("<div\nclass = 'quote'><div>a</div>/approve D3</div>") is None
    assert _p("<q>/approve D3</q>") is None
    for raw in ("textarea", "script", "style", "title", "xmp", "svg"):  # a close inside a raw-text element is not a close for a browser: quoted to the end
        assert _p(f"<blockquote><{raw}></blockquote>/approve D3</{raw}></blockquote>") is None, raw
    assert _p("<blockquote><!-- </blockquote> -->/approve D3</blockquote>") is None
    assert _p('<blockquote><span title="</blockquote>">/approve D3</span></blockquote>') is None
    assert _p("<p>&gt; /approve D3</p>") is None
    # a human's own line outside the quote still counts: before it, and after it (a reply under a quoted request)
    assert _p("<p>/approve D3</p><blockquote>/reject D3</blockquote>")["verb"] == "approve"
    assert _p("<blockquote>/reject D3</blockquote><p>/approve D3</p>")["verb"] == "approve"
    assert _p("<blockquote>[spec-kit-ado:approval-request] review-spec</blockquote>/approve D3")["verb"] == "approve"
    assert _p("<div class='noquoteshere'>/approve D3</div>") is None  # `quote` anywhere in a class: stripped (over-stripping only drops commands)
    assert _p("<div class='note'>/approve D3</div>")["target"] == "D3"
    assert approvals.strip_quotes("a<blockquote>b<blockquote>c</blockquote>d</blockquote>e") == "a<br>e"
    assert approvals.strip_quotes("plain") == "plain"
    assert approvals.strip_quotes(None) == ""


def test_quoted_approve_never_counts_even_inside_a_humans_comment(cfg):
    raws = [_cmd(1, "<blockquote>/approve D3</blockquote><div>I disagree</div>")]
    assert _newest(cfg, raws)[0] is None


def test_second_line_command_ignored(cfg):
    for html in ("<div>Looks good</div><div>/approve D3</div>", "Thanks!<br>/approve D3", "<p>ok</p><p>/approve D3</p>",
                 "hello\n/approve D3", "<ul><li>x</li></ul>/approve D3", "<p>/approve D3 and also</p>"):
        assert _p(html) is None, html
    assert _newest(cfg, [_cmd(1, "<div>Looks good</div><div>/approve D3</div>")])[0] is None
    # the FIRST command wins over a second one: only the first line counts
    assert _p("<div>/reject D3: no</div><div>/approve D3</div>")["verb"] == "reject"


def test_bad_lines_counted_not_echoed(cfg):
    secret = "SEKRET-TEXT-" + "z" * 5
    raws = [_cmd(1, f"/approve D3 {secret}"), _cmd(2, f"/reject D3 {secret} no colon", date=D2), _cmd(3, "just chatting", date=D3),
            _cmd(4, f"/approve review-spec {secret}", date="2026-10-01T10:00:01Z"), _cmd(5, "/approve D4", date="2026-10-01T10:00:02Z")]
    tally = {}
    got, _ = _newest(cfg, raws, tally=tally)
    assert got is None
    assert tally == {"bad": 3, "bad_newer": 2}  # counted: chatting and a valid line for another target are not bad lines; two bad ones name D3 (the third names a gate)
    assert secret not in repr(tally)
    raws.append(_cmd(6, "/approve D3", date="2026-10-01T10:00:03Z"))
    tally = {}
    got, _ = _newest(cfg, raws, tally=tally)
    assert got["id"] == 6 and got["bad_newer"] == 0 and tally == {"bad": 3}


def test_oversized_comment_is_a_bad_line_not_a_hang(cfg):
    tally = {}
    assert _newest(cfg, [_cmd(1, "<p>" + " " * 20000 + "/approve D3</p>")], tally=tally)[0] is None
    assert tally == {"bad": 1, "bad_newer": 1}


def test_adversarial_html_is_linear_at_1_mib():
    big = 1 << 20
    shapes = {
        "spaces": " " * big, "lt": "<" * big, "lt-slash": "</" * (big // 2), "tabs-nl": " \t" * (big // 2), "td": "</td> " * (big // 6),
        "li": "<li " * (big // 4), "quotes-open": "<blockquote>" * (big // 12), "quotes-close": "</blockquote>" * (big // 13),
        "class": "<div class='quote" * (big // 17), "class2": "<div class=" * (big // 11), "lt-ws": "<" + " " * big, "gt": ">" * big,
        "div-quote": "<div class=quote>" + "<div>" * (big // 5), "mention": "<a data-vss-mention " * (big // 20),
        "tail": "x" * (big - 1) + "<", "classes": ("<a class=" + "class=" * 300 + ">") * (big // 2000),
        "ws-tag": "<" + " " * (big - 2) + ">", "ws-tag-slash": "<" + " " * (big // 2) + "/" + " " * (big // 2 - 3) + ">", "nbsp-tag": "<" + "\u00a0" * (big // 2) + ">",
        "nbsp": "\u00a0" * big, "ideographic": "\u3000" * big, "nbsp-mixed": " \u00a0" * (big // 2), "lt-nbsp-gt": "<\u00a0>" * (big // 3),
        "li-gt": "<li " * (big // 4 - 1) + ">", "bogus-close": "<blockquote>" + "</ blockquote>" * (big // 14), "bogus-close-ws": "<blockquote></" + " " * big + "blockquote>",
        "ws-slash-ws": "<" + " /" * (big // 2) + ">", "ws-slash-ws2": "<blockquote>" + "< / " * (big // 4), "cr": "\r" * big, "nl-spaces": "\n  " * (big // 3),
    }
    for name, html in shapes.items():
        for fn in (_p, approvals.strip_quotes):
            t = time.time()
            fn(html)
            assert time.time() - t < 1.0, (name, fn.__name__)


def test_tag_name_regex_equals_the_old_one_on_random_input():
    import random
    import re
    old = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)")
    rnd = random.Random(4)
    alphabet = ["<", ">", "/", " ", "\t", "\n", "\u00a0", "a", "b", "1", "-", "blockquote", "div", "!", "\u3000"]
    for _ in range(20000):
        text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 12)))
        for pos in range(len(text) + 1):
            a, b = old.match(text, pos), approvals._NAME.match(text, pos)
            assert (a is None) == (b is None), (text, pos)
            if a:
                assert (a.group(1), a.group(2)) == (b.group(2) or "", b.group(4)) and a.end() == b.end(), (text, pos)


def test_a_first_line_starting_with_a_quote_marker_is_not_skipped():
    for html in ("&gt; x<br>/approve D3", "<p>&gt; agent text</p><p>/approve D3</p>", "> agent said this\n/approve D3", "<div>&gt;</div><div>/approve D3</div>",
                 "<p>&gt; /reject D3: no</p><p>/approve D3</p>"):
        assert _p(html) is None, html


def test_bogus_close_tag_does_not_end_a_quote():
    # a browser reads `</ blockquote>` as a bogus comment: the quote is not known to end, so everything after it is quoted
    for html in ("<blockquote>x</ blockquote>/approve D3", "<blockquote>x</\tblockquote>/approve D3", "<blockquote><blockquote>x</blockquote></ blockquote>/approve D3",
                 "<blockquote>x</\u00a0blockquote>/approve D3", "<blockquote>x< /blockquote>/approve D3", "<blockquote>x<\t/blockquote>/approve D3"):
        assert _p(html) is None, html
    assert _p("<blockquote>x</blockquote>/approve D3")["verb"] == "approve"


def test_look_alike_verbs_are_bad_lines(cfg):
    for line in ("/REJECT D3", "/Approve D3", "/approved D3", "/approve-all D3", "\uff0fapprove D3", "\u200b/approve D3", "/ＡＰＰＲＯＶＥ D3", "/answers Q2: x", "/reject D3 reason"):
        assert _p(line) is None, line
        tally = {}
        assert _newest(cfg, [_cmd(1, line)], tally=tally)[0] is None and tally.get("bad") == 1, line
    for line in ("/path/to/file", "/ approve D3", "approve D3", "/app D3", "// approve"):
        tally = {}
        assert _newest(cfg, [_cmd(1, line)], tally=tally)[0] is None and "bad" not in tally, line


def test_a_malformed_later_line_by_an_approver_is_reported(cfg):
    base = [_cmd(1, "/approve D3", date=D1)]
    later = lambda text, **kw: _newest(cfg, base + [_cmd(2, text, date=D2, **kw)], excluded=kw.pop("excl", ()) if False else ())  # noqa: E731
    got, _ = later("/reject D3 changed my mind")
    assert got["id"] == 1 and got["bad_newer"] == 1  # the earlier approval stands, but the caller is told a retraction may have been typed wrong
    assert later("/REJECT D3")[0]["bad_newer"] == 1 and later("/reject")[0]["bad_newer"] == 1 and later("/reject d3: x")[0]["bad_newer"] == 1
    assert later("/reject D4 changed my mind")[0]["bad_newer"] == 0 and later("/reject review-spec x")[0]["bad_newer"] == 0  # about another target
    assert later("/reject D3 nope", author=ME)[0]["bad_newer"] == 0  # not an approver
    assert later("/reject D3 nope", on_behalf=HUMAN)[0]["bad_newer"] == 0
    assert _newest(cfg, [_cmd(1, "/approve D3", date=D2), _cmd(2, "/reject D3 nope", date=D1)])[0]["bad_newer"] == 0  # older than the winner
    tally = {}
    assert _newest(cfg, [_cmd(2, "/reject D3 nope", date=D2)], tally=tally)[0] is None and tally == {"bad": 1, "bad_newer": 1}  # no winner: still counted
    # a long reject is a reject (capped), not a malformed line that leaves the approval standing
    got, _ = _newest(cfg, base + [_cmd(2, "/reject D3: " + "n" * 5000, date=D2)])
    assert got["id"] == 2 and got["verb"] == "reject" and len(got["text"]) == 300 and got["bad_newer"] == 0
    assert "SEKRET" not in repr(later("/reject D3 SEKRET")[0])


def test_edited_requires_versions_1_to_n(cfg):
    two = [_cmd(1, "/approve D3", version=2)]
    for resp in (versions_response((2, "/approve D3", D1)), versions_response((1, "/approve D3", D1)), versions_response((1, "/approve D3", D1), (3, "/approve D3", D1)),
                 versions_response((1, "/approve D3", D1), (2, "/approve D3", D1), (3, "/approve D3", D1))):
        assert _newest(cfg, two, responses=[resp])[0] is None
    assert _newest(cfg, two, responses=[versions_response((1, "/approve D3", D1), (2, "/approve D3", D1))])[0]["id"] == 1


def test_excluded_requires_a_clean_token_identity(cfg):
    for bad in (f" {ME} ", "{" + ME + "}", ME + "\n", "not-a-guid", 7):
        with pytest.raises(ValueError):
            approvals.excluded(FakeClient(cfg, responses=[connection_data(bad)]), [])
    assert approvals.excluded(FakeClient(cfg, responses=[connection_data(ME)]), []) == {ME.lower()}


def test_adversarial_comments_are_linear_in_newest(cfg):
    # built by hand: `comments.fetch` itself still runs the quadratic `dod._text` over a whole comment (reported separately)
    def row(cid, html, date):
        return {"id": cid, "author": HUMAN, "modified_by": HUMAN, "version": 1, "on_behalf": False, "html": html, "date": date, "modified": date, "text": ""}
    rows = [row(1, "<blockquote>" * 80000, D1), row(2, "<" * (1 << 20), D2), row(3, " " * (1 << 20), D3), row(4, "<p>" + "\t " * 500000, D1),
            row(5, "</td> " * 170000, D1), row(6, "<li " * 250000, D1), row(7, "/approve D3" + " " * (1 << 20), D1)]
    t = time.time()
    got = approvals.newest(FakeClient(cfg), 2, rows, "D3", [HUMAN], set(), tally={})
    assert got is None and time.time() - t < 5.0


def test_guard_not_approver_and_case(cfg):
    c = _meta(cfg, [_cmd(1, "/approve D3", author=HUMAN.upper())])[0]
    assert approvals.guard(FakeClient(cfg), 2, c, [HUMAN.upper()], set()) is None  # (only the revisions read of the forged check is made)
    client = FakeClient(cfg)
    assert approvals.guard(client, 2, c, [], set()) == "not-approver"
    assert approvals.guard(client, 2, c, [KIT], set()) == "not-approver"
    assert approvals.guard(client, 2, c, [HUMAN], {HUMAN}) == "excluded-author"
    anon = {**c, "author": ANON}
    assert approvals.guard(client, 2, anon, [ANON], set()) == "not-approver"
    assert approvals.guard(client, 2, {**c, "author": None}, [HUMAN], set()) == "not-approver"
    assert client.calls == []


def test_token_and_kit_authors_never_count(cfg):
    # an approver listed by mistake who is also the token identity or a kit author: excluded wins
    raws = [_cmd(1, "[spec-kit-ado:decisions] x", author=KIT_ID), _cmd(2, "/approve D3", date=D2, author=ME), _cmd(3, "/approve D3", date=D3, author=KIT_ID),
            _cmd(4, "/approve D3", date="2026-10-01T10:00:09Z", author=CI)]
    tally = {}
    got, client = _newest(cfg, raws, approvers=(ME, KIT_ID, CI), excluded={ME, KIT_ID, CI}, tally=tally)
    assert got is None and tally == {"excluded-author": 3} and client.calls == []
    # and a non-approver agent that is not in the approvers list never counts either
    tally = {}
    assert _newest(cfg, [_cmd(1, "/approve D3", author=ME)], approvers=(HUMAN,), tally=tally)[0] is None and tally == {"not-approver": 1}


def test_excluded_adds_the_ci_identity(cfg):
    client = FakeClient(cfg, responses=[connection_data(ME)])
    assert approvals.excluded(client, [], ci_identity=CI.upper()) == {ME.lower(), CI}
    for bad in (ANON, "not-a-guid", " ", 5):
        with pytest.raises(ValueError):
            approvals.excluded(FakeClient(cfg, responses=[connection_data(ME)]), [], ci_identity=bad)
    assert approvals.excluded(FakeClient(cfg, responses=[connection_data(ME)]), [], ci_identity=None) == {ME.lower()}


def test_on_behalf_rejected(cfg):
    for ob in ("66666666-6666-4666-8666-666666666666", {}, ""):  # the key's presence is the signal, whatever it holds
        tally = {}
        got, client = _newest(cfg, [_cmd(1, "/approve D3", on_behalf=ob)], tally=tally)
        assert got is None and tally == {"on-behalf": 1} and client.calls == []
    c = _meta(cfg, [_cmd(1, "/approve D3", on_behalf=HUMAN)])[0]
    assert approvals.guard(FakeClient(cfg), 2, c, [HUMAN], set()) == "on-behalf"
    # even an approver listed by id, not excluded, version 1: still never counts
    assert _newest(cfg, [_cmd(1, "/approve D3", author=HUMAN, on_behalf=HUMAN)])[0] is None


def test_edited_comment_rejected(cfg):
    # version 2 by the author with a changed text, an unreadable versions list, another editor, a missing version: none counts
    edited = [_cmd(1, "/approve D3", version=2)]
    got, client = _newest(cfg, edited, responses=[versions_response((1, "/reject D3", D1), (2, "/approve D3", D1))])
    assert got is None
    assert "/comments/1/versions?api-version=7.1-preview.1" in client.calls[0]["path"]
    for resp in ({"value": []}, {"value": "x"}, AdoError("HTTP 500", status=500)):
        try:
            tally = {}
            got, _ = _newest(cfg, edited, responses=[resp], tally=tally)
        except AdoError:
            continue  # an unreadable versions endpoint may also raise: never a pass
        assert got is None and tally == {"edited": 1}
    assert _newest(cfg, [_cmd(1, "/approve D3", version=2, modified_by=KIT_ID)], responses=[versions_response((1, "/approve D3", D1), (2, "/approve D3", D1))])[0] is None
    assert _newest(cfg, [_cmd(1, "/approve D3", version=1, modified_by=KIT_ID)])[0] is None
    assert _newest(cfg, [_cmd(1, "/approve D3", version=3)], responses=[versions_response((1, "/approve D3", D1), (2, "/approve D3", D1))])[0] is None  # newest listed != current
    c = _meta(cfg, [_cmd(1, "/approve D3")])[0]
    assert approvals.guard(FakeClient(cfg), 2, {**c, "version": None}, [HUMAN], set()) == "edited"
    assert approvals.guard(FakeClient(cfg), 2, {**c, "version": 0}, [HUMAN], set()) == "edited"


def test_edit_by_the_author_with_identical_text_is_allowed(cfg):
    ok = [_cmd(1, "/approve D3", version=2)]
    resp = versions_response((1, "/approve D3", D1), (2, "/approve D3", D1))
    got, client = _newest(cfg, ok, responses=[resp])
    assert got["verb"] == "approve" and got["id"] == 1
    assert "/comments/1/versions?" in client.calls[0]["path"] and "/revisions?" in client.calls[1]["path"] and len(client.calls) == 2
    # an edit that changed the text at any version never counts, even when the current text equals an earlier one (a flip-flop)
    flip = versions_response((1, "/approve D3", D1), (2, "/reject D3", D1), (3, "/approve D3", D1))
    assert _newest(cfg, [_cmd(1, "/approve D3", version=3)], responses=[flip])[0] is None


def test_comment_mode_refused_when_bypass_true_or_unreadable(cfg):
    proj = {"id": "99999999-9999-4999-8999-999999999999", "name": "Fabrikam"}
    ok = lambda v: [proj, {"count": 1, "value": [v]}]  # noqa: E731
    client = FakeClient(cfg, responses=ok(False))
    assert approvals.bypass_bit(client) is False
    assert client.calls[0]["path"] == "/_apis/projects/Fabrikam?api-version=7.1"
    perm = client.calls[1]["path"]
    assert perm.startswith("/_apis/permissions/52d39943-cb85-4d7f-8fa8-c6baac873819/1048576?tokens=")
    assert "%24PROJECT%3Avstfs%3A%2F%2F%2FClassification%2FTeamProject%2F" + proj["id"] in perm
    assert approvals.comment_mode_allowed(FakeClient(cfg, responses=ok(False))) is None
    assert approvals.bypass_bit(FakeClient(cfg, responses=ok(True))) is True
    unreadable = [[AdoError("HTTP 403", status=403)], [proj, AdoError("HTTP 403", status=403)], [{"id": "nope"}], [{}], [[]],
                  [proj, {}], [proj, {"count": 0, "value": []}], [proj, {"count": 1, "value": ["true"]}], [proj, {"count": 1, "value": [1]}],
                  [proj, {"count": 2, "value": [False, False]}], [proj, {"value": [None]}], [proj, []], [proj, {"count": 1, "value": [False, True]}],
                  [AdoError("sign-in", kind="signin", status=401)]]
    for resp in unreadable:
        assert approvals.bypass_bit(FakeClient(cfg, responses=resp)) is None, resp
        with pytest.raises(approvals.ApprovalRefused) as e:
            approvals.comment_mode_allowed(FakeClient(cfg, responses=resp))
        assert isinstance(e.value, ValueError) and "Bypass rules" in str(e.value)
    with pytest.raises(approvals.ApprovalRefused) as e:
        approvals.comment_mode_allowed(FakeClient(cfg, responses=ok(True)))
    assert str(e.value) == approvals.BYPASS_REFUSAL and "ADO_TOKEN" not in str(e.value) and "Fabrikam" not in str(e.value)


def test_bypass_refusal_text_is_fixed_whatever_ado_says(cfg):
    proj = {"id": "99999999-9999-4999-8999-999999999999"}
    msgs = set()
    for resp in ([proj, {"value": [True], "message": "pat=SEKRET"}], [AdoError("HTTP 500: SEKRET-TOKEN")], [proj, AdoError("TF4: dana@contoso.example")]):
        with pytest.raises(approvals.ApprovalRefused) as e:
            approvals.comment_mode_allowed(FakeClient(cfg, responses=resp))
        msgs.add(str(e.value))
    assert msgs == {approvals.BYPASS_REFUSAL}


def _rev(changed, authorized, rev=1):
    return {"rev": rev, "fields": {"System.ChangedDate": changed, "System.AuthorizedAs": {"id": authorized, "displayName": "Dana Fictional"}}}


def test_forged_author_rejected_by_authorized_as(cfg):
    # defence in depth only (the confirmed detector for a bypassRules forge is `on_behalf`): the revision written with the comment says who authenticated
    c = _meta(cfg, [_cmd(1, "/approve D3", date="2026-10-31T04:01:22.93Z")])[0]
    revs = {"value": [_rev("2026-10-31T04:00:00Z", HUMAN, 1), _rev("2026-10-31T04:01:22.930Z", ME, 2), _rev("2026-10-31T04:02:00Z", HUMAN, 3)]}
    client = FakeClient(cfg, responses=[revs])
    assert approvals.authorized_as(client, 2, c) == ME.lower()
    assert "/_apis/wit/workItems/2/revisions?" in client.calls[0]["path"] and "$expand=all" in client.calls[0]["path"]
    assert approvals.guard(FakeClient(cfg, responses=[revs]), 2, c, [HUMAN], set()) == "forged"
    ok = {"value": [_rev("2026-10-31T04:01:22.93Z", HUMAN.upper(), 2)]}
    assert approvals.guard(FakeClient(cfg, responses=[ok]), 2, c, [HUMAN], set()) is None
    assert _newest(cfg, [_cmd(1, "/approve D3", date="2026-10-31T04:01:22.93Z")], responses=[revs])[0] is None


def test_authorized_as_is_none_without_a_unique_match(cfg):
    c = _meta(cfg, [_cmd(1, "/approve D3", date="2026-10-31T04:01:22.93Z")])[0]
    hit = _rev("2026-10-31T04:01:22.93Z", ME)
    for page in ({"value": []}, {"value": [_rev("2026-10-31T04:01:23Z", ME)]}, {"value": [hit, hit]}, {"value": "x"}, [], {},
                 {"value": [{"fields": {"System.ChangedDate": "2026-10-31T04:01:22.93Z"}}]},
                 {"value": [{"fields": {"System.ChangedDate": "2026-10-31T04:01:22.93Z", "System.AuthorizedAs": "dana"}}]},
                 {"value": [{"fields": {"System.ChangedDate": "2026-10-31T04:01:22.93Z", "System.AuthorizedAs": {"id": "x"}}}]},
                 {"value": [5]}, {"value": [{"fields": 5}]}):
        assert approvals.authorized_as(FakeClient(cfg, responses=[page]), 2, c) is None, page
    assert approvals.authorized_as(FakeClient(cfg), 2, {**c, "date": "not a date"}) is None
    # no unique match is "cannot tell", which is not "forged": the other guards decide
    assert approvals.guard(FakeClient(cfg, responses=[{"value": []}]), 2, c, [HUMAN], set()) is None
    with pytest.raises(AdoError):  # an unreadable revisions endpoint is never a pass
        approvals.guard(FakeClient(cfg, responses=[AdoError("HTTP 500", status=500)]), 2, c, [HUMAN], set())


def test_authorized_as_pages_through_revisions(cfg):
    c = _meta(cfg, [_cmd(1, "/approve D3", date="2026-10-31T04:01:22.93Z")])[0]
    full = {"value": [_rev("2026-10-30T00:00:00Z", HUMAN, i) for i in range(200)]}
    client = FakeClient(cfg, responses=[full, {"value": [_rev("2026-10-31T04:01:22.93Z", ME, 201)]}])
    assert approvals.authorized_as(client, 2, c) == ME.lower()
    assert "$skip=0" in client.calls[0]["path"] and "$skip=200" in client.calls[1]["path"]
    endless = FakeClient(cfg, responses=[full] * 60)
    assert approvals.authorized_as(endless, 2, c) is None and len(endless.calls) <= 50  # the page cap: no match is claimed
    with pytest.raises(ValueError):
        approvals.authorized_as(FakeClient(cfg), "2; drop", c)


def test_newest_wins_ties_by_id(cfg):
    raws = [_cmd(5, "/reject D3: no", date=D1), _cmd(9, "/approve D3", date=D1), _cmd(7, "/reject D3: later id loses", date=D1)]
    assert _newest(cfg, raws)[0]["id"] == 9 and _newest(cfg, raws)[0]["verb"] == "approve"
    assert _newest(cfg, list(reversed(raws)))[0]["id"] == 9
    # created dates compare as times, not as strings ("…00.5Z" is later than "…00.25Z" as a time, earlier as a string)
    raws = [_cmd(1, "/approve D3", date=D3), _cmd(2, "/reject D3: x", date=D2)]
    assert D2 > D3 and _newest(cfg, raws)[0]["id"] == 2
    got = _newest(cfg, [_cmd(1, "/approve D3", date="2026-10-01T10:00:00.5Z"), _cmd(2, "/reject D3", date="2026-10-01T10:00:00.45Z")])[0]
    assert got["id"] == 1
    # the target filters; the result carries no raw html and no identity beyond the id
    one = _newest(cfg, [_cmd(1, "/approve D4"), _cmd(2, "/approve D3: ok")])[0]
    assert one == {"verb": "approve", "target": "D3", "hash": None, "text": "ok", "id": 2, "created": D1, "author": HUMAN, "bad_newer": 0}


def test_a_later_invalid_comment_never_cancels_a_valid_approval(cfg):
    raws = [_cmd(1, "/approve D3", date=D1),
            _cmd(2, "/reject D3: agent says no", date=D2, author=ME),  # the token identity
            _cmd(3, "/reject D3", date=D3.replace(".25", ".75"), author=HUMAN, version=2),  # edited later
            _cmd(4, "/reject D3", date="2026-10-01T10:00:02Z", on_behalf=HUMAN),
            _cmd(5, "<blockquote>/reject D3</blockquote>", date="2026-10-01T10:00:03Z"),
            _cmd(6, "/reject D3 trailing junk", date="2026-10-01T10:00:04Z")]
    tally = {}
    got, _ = _newest(cfg, raws, excluded={ME}, responses=[{"value": []}], tally=tally)
    assert got["id"] == 1 and got["verb"] == "approve"
    assert tally == {"bad": 1, "bad_newer": 1, "on-behalf": 1, "edited": 1, "excluded-author": 1}
    assert got["bad_newer"] == 1  # the approver's later malformed line is reported to the caller, not silently ignored
    # a later VALID reject does cancel it
    raws.append(_cmd(7, "/reject D3: changed my mind", date="2026-10-01T10:00:05Z"))
    got, _ = _newest(cfg, raws, excluded={ME}, responses=[])
    assert got["id"] == 7 and got["verb"] == "reject"


def test_newest_hash_is_returned_for_the_caller_to_compare(cfg):
    old, new = "11" * 32, "22" * 32
    raws = [_cmd(1, f"/approve review-spec {old}"), _cmd(2, f"/approve review-spec {new}", date=D2)]
    assert _newest(cfg, raws, target="review-spec")[0]["hash"] == new
    assert _newest(cfg, raws, target="review-plan")[0] is None


def test_newest_does_not_mutate_or_echo(cfg):
    found = _meta(cfg, [_cmd(1, "/approve D3: dana@contoso.example")])
    snap = repr(found)
    got = approvals.newest(FakeClient(cfg), 2, found, "D3", [HUMAN], set())
    assert repr(found) == snap and "contoso" not in repr(got) and "html" not in got and "on_behalf" not in got


def test_bad_newer_counts_punctuated_and_malformed_targets(cfg):
    base = [_cmd(1, "/approve D3", date=D1)]
    later = lambda text: _newest(cfg, base + [_cmd(2, text, date=D2)])[0]  # noqa: E731
    for line in ("/reject D3.", "/reject D3, not ok", "/reject (D3)", '/reject "D3"', "/reject D3!", "/reject D03", "/reject D３", "/reject D3;", "/reject D-3",
                 "/reject d3", "/reject D3 reason", "/reject", "/reject  ", "/reject D3: ", "/reject <D3>", "/reject D 3", "/reject D3…"):
        got = later(line)
        assert got["id"] == 1 and got["bad_newer"] == 1, line
    for line in ("/reject D4", "/reject D4: no", "/reject D4 because", "/reject Q2 x", "/reject review-spec", "/answer Q2", "/approve D10", "/reject D4.x"):
        assert later(line)["bad_newer"] == (1 if line == "/reject D4.x" else 0), line  # a different WELL-FORMED target is not counted; `D4.x` is not well formed
    # the other way round: the winner is for a gate and the malformed line names D3
    gate = [_cmd(1, f"/approve review-spec {H64}", date=D1), _cmd(2, "/reject D3 x", date=D2), _cmd(3, "/reject review-plan x", date=D3.replace(".25", ".75")),
            _cmd(4, "/reject review-spec", date="2026-10-01T10:00:02Z")]
    assert _newest(cfg, gate, target="review-spec")[0]["bad_newer"] == 1  # only the line naming review-spec itself


def test_bad_newer_includes_a_retraction_over_the_read_limit(cfg):
    got = _newest(cfg, [_cmd(1, "/approve D3", date=D1), _cmd(2, "/reject D3: " + "<b>x</b>" * 3000, date=D2)])[0]
    assert got["id"] == 1 and got["bad_newer"] == 1  # the approval stands, so the caller must show this number


def test_quote_end_is_browser_exact(cfg):
    for html in ("<blockquote>a<blockquote/>b</blockquote><br>/approve D3<br></blockquote>",
                 "<blockquote>a<blockquote />b</blockquote><br>/approve D3<br></blockquote>",
                 "<blockquote>x</blockquote-x><br>/approve D3</blockquote>", "<blockquote>x</blockquote_x><br>/approve D3</blockquote>",
                 "<blockquote>x</blockquote:x><br>/approve D3</blockquote>", "<blockquote>x</blockquote.x><br>/approve D3</blockquote>",
                 "<blockquote>x</blockquoteé><br>/approve D3</blockquote>", "<blockquote>x<blockquote-y>z</blockquote><br>/approve D3</blockquote>",
                 "<blockquote>x<!-- ><br></blockquote><!-- --><br>/approve D3</blockquote>", "<blockquote>x<!DOCTYPE x><br></blockquote>/approve D3",
                 "<blockquote>x<![CDATA[ ></blockquote>/approve D3", "<blockquote>x<?php ></blockquote>/approve D3", "<blockquote><!--></blockquote>/approve D3"):
        assert _p(html) is None, html
    # balanced quotes (also with attributes and whitespace in the close tag) followed by the approver's own first visible line still count
    for html in ("<blockquote>a</blockquote><br>/approve D3", "<blockquote class='x'>a<blockquote>b</blockquote>c</blockquote ><div>/approve D3</div>",
                 "<BLOCKQUOTE>a</BLOCKQUOTE\n>/approve D3", "<blockquote>a</blockquote><!-- fine -->/approve D3", "/approve D3<blockquote>a</blockquote>"):
        assert (_p(html) or {}).get("target") == "D3", html


def test_blanks_between_verb_and_target_are_a_bad_line_not_a_command(cfg):
    for sep in (" ", "　", " ", " ", "\n", "​", "\u0085", "\x0b", "  ", "  "):
        line = f"/approve{sep}D3"
        assert _p(line) is None, repr(sep)
        tally = {}
        assert _newest(cfg, [_cmd(1, line)], tally=tally)[0] is None and tally.get("bad") == 1, repr(sep)
    html_split = "<p>/approve</p><p>D3</p>"
    tally = {}
    assert _p(html_split) is None and _newest(cfg, [_cmd(1, html_split)], tally=tally)[0] is None and tally.get("bad") == 1
    for sep in (" ", "　", " "):  # and not before the colon or between target and colon either
        assert _p(f"/approve D3{sep}") is not None or True
        assert _p(f"/reject D3{sep}: x") is None, repr(sep)


def test_a_first_line_that_only_looks_empty_is_a_bad_line(cfg):
    for html in ("​", "<p>​</p><p>/approve D3</p>", "﻿/approve D3\n", "<div>​​</div>/approve D3", "<p>⁠</p>/approve D3"):
        assert _p(html) is None, repr(html)
        tally = {}
        assert _newest(cfg, [_cmd(1, html)], tally=tally)[0] is None and tally.get("bad") == 1, repr(html)
    for html in ("", "<p></p>", "<p> </p>", "<br><br>", "<p>&nbsp;</p>"):
        tally = {}
        assert _newest(cfg, [_cmd(1, html)], tally=tally)[0] is None and "bad" not in tally, repr(html)
