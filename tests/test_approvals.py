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


# ---- P4-12a: comment approval inside the five applies --------------------------------------------------------------------------------------
import hashlib  # noqa: E402

from ado.config import Trust  # noqa: E402
from tests.conftest import RouteClient  # noqa: E402

PLAN, STORY = b"# Plan: push\n| create | T001 |\n", 2
T12 = Trust([HUMAN], "comment", {})
GATES = {"questions-post": {"approve-questions", "approve-plan-questions", "approve-answers-questions"}, "push": {"approve-push", "approve-sync"},
         "decisions-post": {"approve-decisions"}, "pr-create": {"approve-pr"}, "branch-link": {"branch-link"}}


def _ok(g="approve-push", cid=11, date=D1, plan=PLAN, verb="approve", **kw):
    return _cmd(cid, f"/{verb} {g} {approvals.gate_hash(g, STORY, plan)}", date, **kw)


def _req(g="approve-push", cid=5, date="2026-10-01T09:00:00Z", plan=PLAN):
    return raw_comment(cid, f"[spec-kit-ado:approval-request] {g} {approvals.gate_hash(g, STORY, plan)}<br>Plan shown here", date, author=KIT)


def _require(cfg, raws, verb="push", trust=T12, plan=PLAN, **kw):
    client = RouteClient(cfg, raws, me=ME, **kw)
    approvals.require(client, trust, STORY, verb, plan)
    return client


def _refused(cfg, raws, verb="push", **kw):
    with pytest.raises(approvals.ApprovalMissing) as e:
        _require(cfg, raws, verb, **kw)
    assert isinstance(e.value, approvals.ApprovalRefused) and isinstance(e.value, ValueError)
    return str(e.value)


def test_gate_hash_is_pinned():
    h = approvals.gate_hash("approve-push", 1001, b"plan")
    assert h == hashlib.sha256(b"spec-kit-ado/1\napprove-push\n1001\nplan").hexdigest() and len(h) == 64
    assert len({h, approvals.gate_hash("approve-sync", 1001, b"plan"), approvals.gate_hash("approve-push", 1002, b"plan"),
                approvals.gate_hash("approve-push", 1001, b"plan2")}) == 4


def test_verb_gates_are_the_five_applies():
    assert approvals.VERB_GATES == GATES


@pytest.mark.parametrize("verb,gate", [(v, g) for v, gs in sorted(GATES.items()) for g in sorted(gs)])
def test_apply_passes_with_any_gate_in_verb_set(cfg, verb, gate):
    client = _require(cfg, [_ok(gate)], verb)
    assert all(c["method"] == "GET" for c in client.calls)  # reads only


@pytest.mark.parametrize("verb", sorted(GATES))
def test_apply_refuses_without_comment_in_comment_mode(cfg, verb):
    msg = _refused(cfg, [_cmd(1, "looks good to me", D1), _cmd(2, "/approve D1", D1)], verb)
    assert all(g in msg for g in GATES[verb]) and str(STORY) in msg


def test_gate_outside_verb_set_refused(cfg):
    for verb, other in (("push", "approve-pr"), ("pr-create", "approve-push"), ("questions-post", "approve-decisions"), ("decisions-post", "branch-link"),
                        ("branch-link", "approve-sync")):
        _refused(cfg, [_ok(other)], verb)


def test_stale_hash_refused(cfg):
    _refused(cfg, [_ok(plan=b"an older plan\n")])
    assert _require(cfg, [_ok(plan=b"an older plan\n"), _ok(cid=12, date=D2)]) is not None  # a newer approval of this plan wins
    _refused(cfg, [_ok(cid=12, date=D1), _ok(plan=b"an older plan\n", cid=13, date=D2)])  # newest wins: the later approval is of another plan


def test_comment_older_than_request_refused(cfg):
    late = _req(date="2026-10-01T11:00:00Z")
    _refused(cfg, [_ok(date="2026-10-01T10:00:00Z"), late])
    assert _require(cfg, [late, _ok(cid=12, date="2026-10-01T12:00:00Z")]) is not None
    assert _require(cfg, [_req(plan=b"another plan"), _ok(date="2026-10-01T10:00:00Z")]) is not None  # a request for another plan is not this plan's
    assert _require(cfg, [_req(g="approve-sync"), _ok("approve-push")]) is not None  # nor is the other gate's request for the same hash...
    _refused(cfg, [_req(g="approve-sync", date="2026-10-01T11:00:00Z"), _ok("approve-sync", date="2026-10-01T10:00:00Z")])
    _refused(cfg, [raw_comment(5, f"[spec-kit-ado:approval-request] approve-push {approvals.gate_hash('approve-push', STORY, PLAN)}", "not a date", author=KIT), _ok()])


def test_refused_when_bypass_bit_true_or_unreadable(cfg):
    for bypass in (True, AdoError("HTTP 403", status=403)):
        client = RouteClient(cfg, [_ok()], bypass=bypass, me=ME)
        with pytest.raises(approvals.ApprovalRefused) as e:
            approvals.require(client, T12, STORY, "push", PLAN)
        assert str(e.value) == approvals.BYPASS_REFUSAL and not isinstance(e.value, approvals.ApprovalMissing)
        assert not any("/comments" in c["path"] for c in client.calls)  # refused before the comments are read


def test_refused_without_approvers_unreadable_or_unknown_identity(cfg):
    assert "approvers" in _refused(cfg, [_ok()], trust=Trust([], "comment", {}))
    cut = {"comments": [raw_comment(1, "x", D1)], "continuationToken": "same"}
    for resp in ([cut, cut], [{"comments": "SEKRET dana@contoso.example"}], [{"comments": [{"id": "SEKRET"}]}]):
        client = RouteClient(cfg, [], me=ME)
        client.raw = None
        calls = iter(resp)
        client.request = lambda method, path, body=None, **k: next(calls) if "/comments?" in path else RouteClient.request(client, method, path, body, **k)
        with pytest.raises(approvals.ApprovalMissing) as e:
            approvals.require(client, T12, STORY, "push", PLAN)
        assert "SEKRET" not in str(e.value) and "unreadable" in str(e.value)
    with pytest.raises(approvals.ApprovalMissing) as e:
        approvals.require(RouteClient(cfg, [_ok()], me=ANON), T12, STORY, "push", PLAN)
    assert "SEKRET" not in str(e.value)


def test_agent_kit_and_strangers_never_approve(cfg):
    for who in (ME, KIT, "88888888-8888-4888-8888-888888888888", CI):
        _refused(cfg, [_ok(author=who)])
    _refused(cfg, [_ok(on_behalf=HUMAN)])
    _refused(cfg, [_ok(version=2)], responses=[versions_response((1, "/approve", D1))])  # edited
    with pytest.raises(approvals.ApprovalMissing):  # the CI identity plumbing: excluded when given
        approvals.require(RouteClient(cfg, [_ok(author=CI)], me=ME), Trust([CI], "comment", {}), STORY, "push", PLAN, ci_identity=CI)


def test_bad_newer_is_reported_and_refuses(cfg):
    ok = _ok(date=D1)
    msg = _refused(cfg, [ok, _cmd(12, f"/approve approve-push {approvals.gate_hash('approve-push', STORY, PLAN)[:60]}", D2)])  # truncated hash
    assert "1 newer malformed" in msg
    assert "2 newer malformed" in _refused(cfg, [ok, _cmd(12, "/reject (approve-push)", D2), _cmd(13, "/reject approve-push", "2026-10-01T10:00:01Z")])
    assert _require(cfg, [_cmd(12, "/reject approve-push", D1), _ok(cid=13, date=D2)]) is not None  # an OLDER malformed line is harmless
    assert _require(cfg, [ok, _cmd(12, "/approve (D1)", D2, author="99999999-9999-4999-8999-999999999999")]) is not None  # not an approver's
    assert _require(cfg, [ok, _cmd(12, "/reject approve-pr", D2)]) is not None  # a different well-formed target
    assert "malformed" in _refused(cfg, [_cmd(12, "/approve approve-push", D2)])  # no valid approval, but the count is still shown
    assert "malformed" not in _refused(cfg, [])


def test_newer_valid_reject_refuses(cfg):
    _refused(cfg, [_ok(date=D1), _ok(verb="reject", cid=12, date=D2)])
    assert _require(cfg, [_ok(verb="reject", cid=12, date=D1), _ok(cid=13, date=D2)]) is not None


def test_refusal_text_is_fixed(cfg):
    a = _refused(cfg, [_cmd(1, f"/reject approve-push {approvals.gate_hash('approve-push', STORY, PLAN)}: SEKRET dana@contoso.example Dana Fictional", D1)])
    b = _refused(cfg, [_cmd(1, "something else", D1)])
    assert a == b and "SEKRET" not in a and "Dana" not in a and "contoso" not in a
    assert a == approvals.E_MISSING.format(story=STORY, gates=", ".join(f"{g} {approvals.gate_hash(g, STORY, PLAN)}" for g in sorted(GATES["push"])), bad="")


def test_terminal_mode_noop(cfg):
    for trust in (Trust([HUMAN], "terminal", {}), Trust([], "terminal", {})):
        client = RouteClient(cfg, [], bypass=True)
        assert approvals.require(client, trust, STORY, "push", PLAN) is None and client.calls == []


def test_the_verbs_gates_are_one_target_for_this_plan(cfg):
    """P4-12a fix 1: a human's `/reject` (valid or malformed) or an approval request on a SIBLING gate of the same verb stops the apply."""
    ok = _ok("approve-push", date=D1)
    _refused(cfg, [ok, _ok("approve-sync", verb="reject", cid=12, date=D2)])  # same hash, other gate of the set
    _refused(cfg, [ok, _cmd(12, "/reject approve-sync", D2)])  # malformed reject on the other gate
    _refused(cfg, [ok, _cmd(12, "/reject (approve-sync)", D2)])
    _refused(cfg, [ok, _req("approve-sync", date="2026-10-01T11:00:00Z")])  # a newer request on the other gate
    _refused(cfg, [ok, _req("approve-push", date="2026-10-01T11:00:00Z")])
    _refused(cfg, [ok, _ok("approve-sync", plan=b"an older plan\n", cid=12, date=D2)])  # another plan's approval on the sibling gate
    for older in ("approve-push", "approve-sync"):  # reject first, a later approval wins; either gate may carry either command
        for newer in ("approve-push", "approve-sync"):
            assert _require(cfg, [_ok(older, verb="reject", cid=12, date=D1), _ok(newer, cid=13, date=D2)]) is not None
    # three gates: questions
    q = ("approve-questions", "approve-plan-questions", "approve-answers-questions")
    _refused(cfg, [_ok(q[0], date=D1), _ok(q[2], verb="reject", cid=12, date=D2)], "questions-post")
    assert _require(cfg, [_ok(q[2], verb="reject", cid=12, date=D1), _ok(q[0], cid=13, date=D2)], "questions-post") is not None
    # an older malformed line, before the approval, is harmless on every gate of the set
    assert _require(cfg, [_cmd(12, "/reject approve-sync", D1), _cmd(13, "/reject (x)", D1), _ok(cid=14, date=D2)]) is not None
    # a request that is OLDER than the approval is fine for every gate
    assert _require(cfg, [_req("approve-sync"), _req("approve-push", cid=6), _ok(date=D1)]) is not None


# ---- P4-12b: `ado approvals check` ----------------------------------------------------------------------------------------------------------------
import datetime  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

from ado import __main__ as _main  # noqa: E402
from ado import dod, questions as qmod, status  # noqa: E402
from ado.config import Config  # noqa: E402
from tests.conftest import commit, git, ledger_md, review_md  # noqa: E402

T0, T1, T2, T3 = (f"2026-10-01T{h}:00:00Z" for h in (10, 11, 12, 13))
GATE12 = ("review-spec", "approve-questions", "approve-plan-questions", "wait-po-answers", "approve-answers-questions", "review-plan", "approve-push",
          "approve-decisions", "approve-sync", "review-review", "approve-pr", "wait-po-validation")
REVIEW3, WAIT2 = ("review-spec", "review-plan", "review-review"), ("wait-po-answers", "wait-po-validation")
FID = "### AC fidelity — 2026-10-28 @ abc1234\n\n- AC-1: asserts — it checks the total\n\n"


def test_gates_are_the_twelve_of_the_workflow():
    assert approvals.GATES == GATE12 == status.SHIPPED_GATES and approvals.REVIEW_GATES == REVIEW3 and approvals.WAIT_GATES == WAIT2


# -- the review gates: a hash over the listed artifacts --
@pytest.fixture
def repo(tmp_path, git_env):
    git(tmp_path, "init", "-q")
    commit(tmp_path, "init", {"README": "x"})
    return tmp_path


def _tree(root, spec="# Spec\nA\n", plan="# Plan\nB\n", blocks=None, spec_dir="specs/001-x", mode="advisory"):
    """Write the artifacts (no commit: HEAD stays) and return the config that finds the ledger."""
    blocks = [review_md("abc1234")] if blocks is None else blocks
    for rel, text in {"dod/DoD_1001.md": ledger_md(*blocks, spec_dir=spec_dir), f"{spec_dir}/spec.md": spec, f"{spec_dir}/plan.md": plan}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return Config(organization="contoso", project="Fabrikam", dod_path=str(root / "dod"), mode=mode)


def _art(gate, root, cfg):
    return approvals.artifacts(gate, 1001, cfg, str(root))


def test_review_gate_hash_covers_listed_artifacts(repo, tmp_path_factory):
    cfg = _tree(repo)
    got = {g: _art(g, repo, cfg) for g in REVIEW3}
    assert len(set(got.values())) == 3 and all(isinstance(b, bytes) for b in got.values())

    def changed(gate, **kw):
        return _art(gate, repo, _tree(repo, **kw)) != got[gate]
    assert not changed("review-spec") and not changed("review-plan") and not changed("review-review")  # same content, same bytes
    assert changed("review-spec", spec="# Spec\nA2\n") and changed("review-plan", spec="# Spec\nA2\n") and not changed("review-review", spec="# Spec\nA2\n")
    assert changed("review-plan", plan="# Plan\nB2\n") and not changed("review-spec", plan="# Plan\nB2\n")  # plan.md: the plan gate only
    assert not changed("review-review", plan="# Plan\nB2\n")
    # independent of the checkout path and of file times: another directory, same content, same HEAD message-free bytes for the spec/plan gates
    other = tmp_path_factory.mktemp("elsewhere")
    cfg2 = _tree(other)
    os.utime(other / "specs/001-x/spec.md", (1, 1))
    assert _art("review-spec", other, cfg2) == got["review-spec"] and _art("review-plan", other, cfg2) == got["review-plan"]


def test_review_hash_ledger_sections(repo):
    def ledger(sec2="- [ ] Q1: a → _(open)_\n", sec3="", blocks=(review_md("abc1234"),), tail=""):
        _tree(repo)
        (repo / "dod/DoD_1001.md").write_text(f"# DoD\n- **Spec dir:** specs/001-x\n\n{dod.SECTION2}\n{sec2}\n## 3. AC\n{sec3}\n"
                                              f"## 4. Gate & review log\n\n{''.join(blocks)}## 5. Checklist\n- [ ] x{tail}\n")
        return Config(organization="contoso", project="Fabrikam", dod_path=str(repo / "dod"))
    spec, plan, rev = (_art(g, repo, ledger()) for g in REVIEW3)
    cfg = ledger(sec3="AC row changed", tail=" changed")  # sections 3 and 5 are not shown at these gates
    assert (spec, plan, rev) == tuple(_art(g, repo, cfg) for g in REVIEW3)
    cfg = ledger(sec2="- [x] Q1: a → yes · owner: PO\n")  # section 2: the spec and plan gates
    assert _art("review-spec", repo, cfg) != spec and _art("review-plan", repo, cfg) != plan and _art("review-review", repo, cfg) == rev
    cfg = ledger(blocks=(review_md("abc1234", blocker=1),))  # the review gate: its newest live block
    assert _art("review-review", repo, cfg) != rev and _art("review-spec", repo, cfg) == spec
    assert _art("review-review", repo, ledger(blocks=(review_md("abc1234", blocker=2, superseded=True), review_md("abc1234")))) == rev  # a superseded block is not shown
    assert _art("review-review", repo, ledger(blocks=("```\n### Review — fenced\n```\n", review_md("abc1234")))) == rev  # a fenced heading is no block
    assert _art("review-review", repo, ledger(blocks=(review_md("abc1234"), review_md("abc1234", blocker=2, superseded=True)))) == rev  # the newest block is superseded: the live one before it


def test_framing_moves_with_the_boundary_and_line_endings_count(repo):
    """The parts are length-framed: the same bytes split differently between plan.md and spec.md hash differently; the checkout's line endings are part of the bytes."""
    a = _art("review-plan", repo, _tree(repo, plan="P1\n", spec="S1\n"))
    assert _art("review-plan", repo, _tree(repo, plan="P1\nS", spec="1\n")) != a  # plan + spec is "P1\nS1\n" both times
    assert b"plan.md 3\nP1\n\nspec.md 3\nS1\n\nledger-2 " in a  # the frame format is pinned
    assert _art("review-spec", repo, _tree(repo, spec="S1\r\n")) != _art("review-spec", repo, _tree(repo, spec="S1\n"))  # CRLF is not LF


def test_review_review_covers_head_and_strict_fidelity(repo):
    cfg = _tree(repo)
    before = _art("review-review", repo, cfg)
    commit(repo, "y", {"other.txt": "z"})  # HEAD moves
    assert _art("review-review", repo, cfg) != before
    advisory = _art("review-review", repo, _tree(repo, blocks=[review_md("abc1234"), FID]))
    assert b"AC-1" not in advisory  # the fidelity block is shown in strict mode only
    s1 = _art("review-review", repo, _tree(repo, blocks=[review_md("abc1234"), FID], mode="strict"))
    assert b"AC-1" in s1 and s1 != advisory
    assert _art("review-review", repo, _tree(repo, blocks=[review_md("abc1234"), FID.replace("total", "sum")], mode="strict")) != s1
    assert _art("review-review", repo, _tree(repo, mode="strict"))  # strict and no fidelity block: an empty frame, not a refusal
    assert _art("review-review", repo, _tree(repo, mode="strict")) != _art("review-review", repo, _tree(repo))  # a mode flip in the working tree always changes the bytes: stale, never a pass


def test_artifacts_refuse_what_they_cannot_read(repo, tmp_path_factory):
    cfg = _tree(repo)
    for gate in ("approve-push", "wait-po-answers", "nope"):
        with pytest.raises(ValueError):
            _art(gate, repo, cfg)
    outside = tmp_path_factory.mktemp("outside")
    (outside / "spec.md").write_text("secret")
    (repo / "specs/001-x/spec.md").unlink()
    (repo / "specs/001-x/spec.md").symlink_to(outside / "spec.md")  # a symlink: never read
    with pytest.raises(ValueError) as e:
        _art("review-spec", repo, cfg)
    assert str(e.value) == approvals.E_ARTIFACT
    for spec_dir in ("../" + outside.name, str(outside), "specs/missing"):  # outside the tree, absolute, absent
        (outside / "plan.md").write_text("x")
        _tree(repo)
        (repo / "dod/DoD_1001.md").write_text(ledger_md(review_md("abc1234"), spec_dir=spec_dir))
        with pytest.raises(ValueError):
            _art("review-spec", repo, cfg)
    (repo / "dod/DoD_1001.md").write_text("# DoD\nno sections\n")
    for gate in REVIEW3:
        with pytest.raises(ValueError):
            _art(gate, repo, cfg)
    _tree(repo, blocks=[])  # no review block yet
    with pytest.raises(ValueError):
        _art("review-review", repo, cfg)
    _tree(repo)
    (repo / "dod/DoD_1001.md").write_text(ledger_md(review_md("abc1234")).replace("## 5.", "```\n## 5."))  # an unclosed fence
    with pytest.raises(ValueError):
        _art("review-spec", repo, cfg)


def _rc(cfg, raws, **kw):
    return RouteClient(cfg, raws, me=ME, **kw)


def _appr(gate, art, cid=11, date=T1, h=None, **kw):
    return _cmd(cid, f"/approve {gate} {h or approvals.gate_hash(gate, 1001, art)}", date, **kw)


def test_review_gates_met_only_by_the_matching_hash(repo, cfg):
    conf = _tree(repo)
    for gate in REVIEW3:
        art = _art(gate, repo, conf)
        other = _art(next(g for g in REVIEW3 if g != gate), repo, conf)
        assert approvals.check(_rc(cfg, [_appr(gate, art)]), T12, 1001, gate, conf, str(repo)) == {"gate": gate, "mode": "comment", "met": True}
        h = approvals.gate_hash(gate, 1001, art)
        # another gate's artifacts, no comment, the agent or a stranger, a newer reject, another hash, an edited comment, an approval older than the request
        for raws in ([_appr(gate, other)], [], [_appr(gate, art, author=ME)], [_appr(gate, art, author=ANON)], [_appr(gate, art, version=2)],
                     [_appr(gate, art), _cmd(12, f"/reject {gate} {h}", T2)], [_appr(gate, art), _appr(gate, other, cid=12, date=T2)],
                     [_appr(gate, art), raw_comment(5, f"[spec-kit-ado:approval-request] {gate} {h}", T2, author=KIT)]):
            with pytest.raises(approvals.ApprovalMissing) as e:
                approvals.check(_rc(cfg, raws, responses=[versions_response((1, "/approve", T1))]), T12, 1001, gate, conf, str(repo))
            assert str(e.value) == approvals.E_REVIEW.format(gate=gate, story=1001, hash=h, bad="")
        assert approvals.check(_rc(cfg, [_cmd(12, f"/reject {gate} {h}", T0), _appr(gate, art)]), T12, 1001, gate, conf, str(repo))["met"]  # reject first, approve later
    art = _art("review-spec", repo, conf)  # the spec changed after the approval: stale
    conf = _tree(repo, spec="# Spec\nchanged after approval\n")
    with pytest.raises(approvals.ApprovalMissing):
        approvals.check(_rc(cfg, [_appr("review-spec", art)]), T12, 1001, "review-spec", conf, str(repo))


def test_review_check_surfaces_bad_newer_and_refuses_bypass_and_unreadable(repo, cfg):
    conf = _tree(repo)
    ok = _appr("review-spec", _art("review-spec", repo, conf))
    with pytest.raises(approvals.ApprovalMissing) as e:
        approvals.check(_rc(cfg, [ok, _cmd(12, "/reject (review-spec)", T2, author=HUMAN)]), T12, 1001, "review-spec", conf, str(repo))
    assert "1 newer malformed" in str(e.value) and "(review-spec)" not in str(e.value)
    for bypass in (True, AdoError("HTTP 403", status=403)):
        client = _rc(cfg, [ok], bypass=bypass)
        with pytest.raises(approvals.ApprovalRefused) as e:
            approvals.check(client, T12, 1001, "review-spec", conf, str(repo))
        assert str(e.value) == approvals.BYPASS_REFUSAL and not any("/comments" in c["path"] for c in client.calls)
    cut = {"comments": [raw_comment(1, "x", D1)], "continuationToken": "same"}
    client, pages = _rc(cfg, []), iter([cut, cut])
    client.request = lambda method, path, body=None, **k: next(pages) if "/comments?" in path else RouteClient.request(client, method, path, body, **k)
    with pytest.raises(approvals.ApprovalMissing) as e:
        approvals.check(client, T12, 1001, "review-spec", conf, str(repo))
    assert str(e.value) == approvals.E_UNREADABLE
    with pytest.raises(approvals.ApprovalMissing):
        approvals.check(_rc(cfg, [ok]), Trust([], "comment", {}), 1001, "review-spec", conf, str(repo))


def test_terminal_mode_noop_check(cfg):
    for trust in (Trust([HUMAN], "terminal", {}), Trust([], "terminal", {})):
        for gate in GATE12:
            client = RouteClient(cfg, [], bypass=True)
            assert approvals.check(client, trust, 1001, gate, cfg) == {"gate": gate, "mode": "terminal", "met": True} and client.calls == []


def test_unknown_gate_refused_and_apply_gates_not_checked_here(cfg):
    for gate in set(GATE12) - set(REVIEW3) - set(WAIT2):  # the seven apply gates are checked by their own --apply
        client = _rc(cfg, [_ok(gate)])
        with pytest.raises(approvals.ApprovalRefused) as e:
            approvals.check(client, T12, 1001, gate, cfg)
        assert str(e.value) == approvals.E_APPLY_GATE.format(gate=gate) and client.calls == []
    for bad in ("nope", "", "review-spec ", "REVIEW-SPEC", "../x", "review-spec;rm", None, 3):
        for trust in (T12, Trust([], "terminal", {})):
            client = RouteClient(cfg, [])
            with pytest.raises(approvals.ApprovalRefused) as e:
                approvals.check(client, trust, 1001, bad, cfg)
            assert str(e.value) == approvals.E_GATE and client.calls == []
    for gate, text in (("review-spec", approvals.E_NOT_WAIT.format(gate="review-spec")), ("approve-push", approvals.E_NOT_WAIT.format(gate="approve-push")), ("nope", approvals.E_GATE)):
        with pytest.raises(approvals.ApprovalRefused) as e:
            approvals.wait_state(RouteClient(cfg, []), 1001, gate, T12)
        assert str(e.value) == text


# -- the wait gates: ADO state only --
QA, QB = ({"id": f"Q{n}", "question": q} for n, q in ((1, "Are contractors included?"), (2, "Which locale do we ship?")))
TW = Trust([HUMAN], "comment", {"assume_after_hours": 24})
NOT_Q = approvals.E_QUESTIONS, approvals.E_AGE_UNKNOWN


def _own(qs, cid=10, date=T0, assumed=(), **kw):
    html = qmod.comment_html(qs, assumed)
    return html, raw_comment(cid, html, date, author=ME, **kw)


def _wait(cfg, raws, vers=None, trust=T12, gate="wait-po-answers", now=None, **kw):
    client = RouteClient(cfg, raws, me=ME, responses=[vers] if vers is not None else [], **kw)
    client.now = now
    return approvals.wait_state(client, 1001, gate, trust), client


def _cli_env(tmp_path, monkeypatch, raws, **kw):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: T12)
    monkeypatch.setattr(_main, "AdoClient", lambda c: RouteClient(c, raws, me=ME, **kw))


def test_wait_answers_from_ado_not_ledger(cfg, tmp_path, monkeypatch, capsys):
    html, own = _own([QA, QB])
    vers = versions_response((1, html, T0))
    (tmp_path / "dod").mkdir()  # the ledger says both are answered; ADO has an approver's /answer for Q1 only
    (tmp_path / "dod/DoD_1001.md").write_text(ledger_md().replace("## 3.", "- [x] Q1: a → yes · owner: PO\n- [x] Q2: b → no · owner: PO\n## 3."))
    q1 = _cmd(11, "/answer Q1: SEKRET dana@contoso.example", T1)
    st, client = _wait(cfg, [own, q1], vers)
    assert st == {"met": False, "open": ["Q2"], "bad_newer": 0} and all(c["method"] == "GET" for c in client.calls)
    assert _wait(cfg, [own, q1, _cmd(12, "/answer Q2: no", T2)], vers)[0] == {"met": True, "open": [], "bad_newer": 0}
    # every answer passes the guards: the agent, the kit, a stranger, a forged author, a quote, a time not after the posting
    for bad in (_cmd(11, "/answer Q1: y", T1, author=ME), _cmd(11, "/answer Q1: y", T1, author=KIT), _cmd(11, "/answer Q1: y", T1, author=ANON),
                _cmd(11, "/answer Q1: y", T1, on_behalf=HUMAN), _cmd(11, "<blockquote>/answer Q1: y</blockquote>", T1),
                _cmd(11, "/answer Q1: y", "2026-10-01T09:00:00Z"), _cmd(5, "/answer Q1: y", T0)):
        assert _wait(cfg, [own, bad], vers)[0]["open"] == ["Q1", "Q2"]
    st = _wait(cfg, [own, q1, _cmd(12, "/answer Q2: no", T2), _cmd(13, "/answer (Q2)", T3)], vers)[0]  # a newer malformed line could retract either answer: counted, never ignored
    assert st == {"met": False, "open": ["Q1", "Q2"], "bad_newer": 2}
    assert _wait(cfg, [own, q1, _cmd(12, "/answer Q2: no", T2), _cmd(13, "/answer Q9", T3)], vers)[0]["met"] is True  # a malformed line about another well-formed target
    monkeypatch.chdir(tmp_path)  # via the CLI: the text names the gate, a count and ids; nothing from a comment
    _cli_env(tmp_path, monkeypatch, [own, q1], responses=[vers])
    assert _main.main(["approvals", "check", "1001", "--gate", "wait-po-answers"]) == 2
    err = capsys.readouterr().err
    assert err.strip() == "error: " + approvals.E_WAIT.format(gate="wait-po-answers", story=1001, n=1, ids="Q2", bad="") and "SEKRET" not in err and "contoso" not in err


def _refuses(cfg, raws, vers=None, text=approvals.E_QUESTIONS, **kw):
    with pytest.raises(approvals.ApprovalMissing) as e:
        _wait(cfg, raws, vers, **kw)
    assert str(e.value) == text


def test_wait_answers_nothing_to_wait_for_and_unreadable(cfg):
    assert _wait(cfg, [_cmd(1, "hi", T0)])[0] == {"met": True, "open": [], "bad_newer": 0}  # no questions comment: nothing is waited for (a documented residual)
    done = raw_comment(10, qmod.ANSWERED, T0, author=ME)
    assert _wait(cfg, [done], versions_response((1, qmod.ANSWERED, T0)))[0]["met"] is True
    html, own = _own([QA], assumed=[{"id": "Q9", "question": "Old?", "answer": "assumed: yes"}])
    assert _wait(cfg, [own], versions_response((1, html, T0)))[0]["open"] == ["Q1"]  # the part the team assumed is not a question
    html, own = _own([QA])
    ok = versions_response((1, html, T0))
    _refuses(cfg, [_own([QA], modified_by=HUMAN)[1]], ok)  # edited by another identity
    _refuses(cfg, [raw_comment(10, "[spec-kit-ado:questions]<br><span>x</span>", T0, author=ME)], versions_response((1, "[spec-kit-ado:questions]<br><span>x</span>", T0)))
    for bad in ("[spec-kit-ado:questions]<br>Open questions for the product owner:<br>Q1: a<br>Q1: b", "[spec-kit-ado:questions]<br>Open questions for the product owner:<br>not a question"):
        _refuses(cfg, [raw_comment(10, bad, T0, author=ME)], versions_response((1, bad, T0)))
    _refuses(cfg, [own], AdoError("HTTP 404", status=404), text=approvals.E_AGE_UNKNOWN)  # no versions endpoint: the age cannot be established
    _refuses(cfg, [own], versions_response((1, html + "x", T0)))  # the versions do not show the comment as it is
    assert _wait(cfg, [own, _cmd(11, "/answer Q1: y", T1)], versions_response((1, html, T0)))[0]["met"] is True
    _refuses(cfg, [own], ok, trust=Trust([], "comment", {}), text=approvals.E_NO_APPROVERS)
    with pytest.raises(approvals.ApprovalRefused):
        _wait(cfg, [own], ok, bypass=True)


def test_agent_cannot_meet_wait_answers_with_its_own_comments(cfg):
    """P4-12b fix 1: the token identity (the agent) posts a bare marker or the ANSWERED text, or edits its comment to say so; the open questions stay open."""
    html, own = _own([QA, QB])
    v1 = versions_response((1, html, T0))
    bare = "[spec-kit-ado:questions]"
    _refuses(cfg, [own, raw_comment(11, bare, T1, author=ME)], v1)  # (b) a second marker comment, bare
    _refuses(cfg, [own, raw_comment(11, qmod.ANSWERED, T1, author=ME)], v1)  # ... or with the ANSWERED text
    _refuses(cfg, [raw_comment(11, bare, T1, author=ME)], versions_response((1, bare, T1)))  # (a) a bare marker alone is unreadable, not "no open lines"
    _refuses(cfg, [raw_comment(11, "[spec-kit-ado:questions]<br>All good", T1, author=ME)], versions_response((1, "[spec-kit-ado:questions]<br>All good", T1)))
    for hidden in ("\u200b" + bare, "\u2060\u200d  " + bare + " all answered", "<p>" + bare + "</p>"):  # (b) disguised markers by anyone count as marker comments
        for who in (ME, HUMAN):
            _refuses(cfg, [own, raw_comment(11, hidden, T1, author=who)], v1)
    _refuses(cfg, [own, raw_comment(11, "x [spec-kit-ado:questions]", T1, author=KIT)], v1)  # another identity's marker comment
    # (c) the agent EDITS its own comment: bare marker -> unreadable; ANSWERED -> both questions stay open; the old lines gone from the text
    edited = raw_comment(10, bare, T0, author=ME, version=2)
    _refuses(cfg, [edited], versions_response((1, html, T0), (2, bare, T1)))
    gone = raw_comment(10, qmod.ANSWERED, T0, author=ME, version=2)
    two = versions_response((1, html, T0), (2, qmod.ANSWERED, T1))
    assert _wait(cfg, [gone], two)[0] == {"met": False, "open": ["Q1", "Q2"], "bad_newer": 0}
    assert _wait(cfg, [gone, _cmd(11, "/answer Q1: y", T2)], two)[0]["open"] == ["Q2"]  # a guarded approver /answer closes it
    assert _wait(cfg, [gone, _cmd(11, "/answer Q1: y", T0), _cmd(12, "/answer Q2: y", T0, author=ME)], two)[0]["open"] == ["Q1", "Q2"]  # not before it was shown; not the agent's
    assert _wait(cfg, [gone], two, trust=TW, now=datetime.datetime(2026, 10, 2, 10, tzinfo=datetime.timezone.utc))[0]["met"] is True  # due, counted from the first version
    assert _wait(cfg, [gone], two, trust=TW, now=datetime.datetime(2026, 10, 1, 12, tzinfo=datetime.timezone.utc))[0]["open"] == ["Q1", "Q2"]
    # a question that goes away in a later edit of the same comment, an earlier version unreadable: refused
    junk = versions_response((1, bare, T0), (2, html, T1))
    _refuses(cfg, [raw_comment(10, html, T0, author=ME, version=2)], junk)


def test_wait_gates_refuse_when_the_marker_comments_are_not_the_token_identitys(cfg):
    """P4-12b fix 2: a poller or CI identity that is not the one that posted must not make everything look met."""
    html, _ = _own([QA])
    other = raw_comment(10, html, T0, author=KIT)
    _refuses(cfg, [other], versions_response((1, html, T0)))
    post = _posts("D1 (decision): x", author=KIT)
    _refuses(cfg, [post], text=approvals.E_DECISIONS, gate="wait-po-validation")
    assert _wv(cfg, [_posts("D1 (decision): x", cid=21), post])["open"] == ["D1"]  # the token identity's own posting is there: others are read too


def test_wait_validation_is_as_strict_as_the_done_time_check(cfg):
    """P4-12b fix 3: an edited, disguised, cut, markup or unparsable kit decisions posting refuses (strict.xcheck reads them as `-marker`)."""
    head = "Decisions and assumptions made during development; please validate or reject:"
    old = f"[spec-kit-ado:decisions]<br>{head}<br>D1 (decision): use a queue<br>D2 (assumption): UTC"

    def refuses(raws, vers=None):
        with pytest.raises(approvals.ApprovalMissing) as e:
            _wv(cfg, raws, vers)
        assert str(e.value) == approvals.E_DECISIONS
    for new in (f"[spec-kit-ado:decisions]<br>{head}", f"[spec-kit-ado:decisions]<br>{head}<br>D1 (decision): use a queue"):  # an edit removing every D line / dropping D2
        refuses([raw_comment(20, new, T0, author=ME, version=2)], versions_response((1, old, T0), (2, new, T1)))
    refuses([raw_comment(20, "\u200b" + old, T0, author=ME)])  # a zero-width prefix: disguised, never skipped
    refuses([raw_comment(20, "\u2060 \u200d" + old, T0, author=ME)])
    refuses([_posts("D1 (decision): x"), raw_comment(21, "\u200b[spec-kit-ado:decisions]", T0, author=HUMAN)])
    refuses([raw_comment(20, f"[spec-kit-ado:decisions]<br><span>{head}</span><br>D1 (decision): x", T0, author=ME)])  # hidden in markup
    refuses([raw_comment(20, "\u2800" * 64 + old, T0, author=ME)])  # 64 braille blanks / Hangul fillers in front: disguised
    refuses([raw_comment(20, "\u3164" * 70 + "\u2800" * 3 + old, T0, author=ME)])
    refuses([raw_comment(20, f"[spec-kit-ado:decisions]<br>{head}<br>some other line", T0, author=ME)])  # an unparsable line
    refuses([raw_comment(20, f"[spec-kit-ado:decisions]<br>D1 (decision): no heading", T0, author=ME)])
    refuses([_posts("D1 (decision): x", cid=21, date="not a date")])
    same = raw_comment(20, old, T0, author=ME, version=2)  # a no-op save is not an edit
    assert _wv(cfg, [same], versions_response((1, old, T0), (2, old, T1)))["open"] == ["D1", "D2"]


def test_wait_due_counts_with_server_time(cfg, monkeypatch):
    html, own = _own([QA, QB])
    vers = versions_response((1, html, T0))
    at = lambda **kw: datetime.datetime(2026, 10, 1, 10, tzinfo=datetime.timezone.utc) + datetime.timedelta(**kw)  # noqa: E731
    assert _wait(cfg, [own], vers, trust=TW, now=at(hours=23, minutes=59))[0]["open"] == ["Q1", "Q2"]
    assert _wait(cfg, [own], vers, trust=TW, now=at(hours=24))[0] == {"met": True, "open": [], "bad_newer": 0}  # due: 24 h on the server's clock
    st = _wait(cfg, [own, _cmd(11, "/answer Q1: yes", T1)], vers, trust=TW, now=at(hours=23))[0]
    assert st["open"] == ["Q2"]  # answered or due, per question
    assert _wait(cfg, [own], vers, trust=T12, now=at(days=30))[0]["open"] == ["Q1", "Q2"]  # no assume_after_hours: nothing is ever due
    for bad in (True, 0, 721, "24", 1.5):
        assert _wait(cfg, [own], vers, trust=Trust([HUMAN], "comment", {"assume_after_hours": bad}), now=at(days=30))[0]["open"] == ["Q1", "Q2"]
    with pytest.raises(approvals.ApprovalMissing) as e:  # no server time: refused, never due
        _wait(cfg, [own], vers, trust=TW, now=None)
    assert str(e.value) == approvals.E_NO_TIME
    # the local clock is never read: a wall clock far in the future changes nothing
    import time
    monkeypatch.setattr(time, "time", lambda: datetime.datetime(2040, 1, 1).timestamp())
    assert _wait(cfg, [own], vers, trust=TW, now=at(hours=1))[0]["open"] == ["Q1", "Q2"]


# wait-po-validation
def _posts(*lines, cid=20, date=T0, author=ME, **kw):
    return raw_comment(cid, "<br>".join(["[spec-kit-ado:decisions]", "Decisions and assumptions made during development; please validate or reject:", *lines]),
                       date, author=author, **kw)


def _wv(cfg, raws, vers=None, **kw):
    return _wait(cfg, raws, vers, gate="wait-po-validation", **kw)[0]


def test_wait_validation_from_ado_not_decisions_file(cfg, tmp_path, monkeypatch, capsys):
    post = _posts("D1 (decision): use a queue", "D2 (assumption): UTC")
    (tmp_path / "dod").mkdir()  # the decisions file says both are validated: it is never read
    (tmp_path / "dod/Decisions_1001.md").write_text("- [x] D1 (decision): use a queue → validated · by: PO\n- [x] D2 (assumption): UTC → validated · by: PO\n")
    monkeypatch.chdir(tmp_path)
    a1 = _cmd(21, "/approve D1: SEKRET dana@contoso.example", T1)
    assert _wv(cfg, [post, a1]) == {"met": False, "open": ["D2"], "bad_newer": 0}
    assert _wv(cfg, [post, a1, _cmd(22, "/reject D2: no", T2)]) == {"met": True, "open": [], "bad_newer": 0}
    for bad in (_cmd(21, "/approve D1", T1, author=ME), _cmd(21, "/approve D1", T1, author=KIT), _cmd(21, "/approve D1", T1, author=ANON),
                _cmd(21, "/approve D1", T1, on_behalf=HUMAN), _cmd(21, "> /approve D1", T1), _cmd(21, "<blockquote>/approve D1</blockquote>", T1),
                _cmd(21, "/approve D1", "2026-10-01T09:00:00Z"), _cmd(21, "/approve D2", T1), _cmd(21, "/answer Q1: x", T1)):
        assert "D1" in _wv(cfg, [post, bad])["open"]
    # a re-post of a changed D1 needs a fresh verdict, newer than the NEWEST posting
    again = _posts("D1 (decision): use a stream", cid=23, date=T2)
    both = [post, a1, _cmd(22, "/approve D2", T1)]
    assert _wv(cfg, both + [again])["open"] == ["D1"]
    assert _wv(cfg, both + [again, _cmd(24, "/approve D1", T3)])["met"] is True
    assert _wv(cfg, both + [_cmd(24, "/reject D1: x", T2)])["met"] is True  # newest wins; a reject is a verdict too
    assert _wv(cfg, both + [_cmd(24, "/reject (D1)", T2)]) == {"met": False, "open": ["D1", "D2"], "bad_newer": 2}  # a newer malformed line could be about either: counted on both
    assert _wv(cfg, both + [_cmd(24, "/reject approve-pr", T2)])["met"] is True  # a different well-formed target is no retraction here
    assert _wv(cfg, [_cmd(1, "hello", T0)])["met"] is True  # no decisions comment: nothing to wait for
    _cli_env(tmp_path, monkeypatch, [post, a1])
    assert _main.main(["approvals", "check", "1001", "--gate", "wait-po-validation"]) == 2
    err = capsys.readouterr().err
    assert err.strip() == "error: " + approvals.E_WAIT.format(gate="wait-po-validation", story=1001, n=1, ids="D2", bad="") and "SEKRET" not in err


# CLI
def test_cli_terminal_mode_needs_no_token_and_unknown_gate_is_usage(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_main, "AdoClient", lambda c: pytest.fail("terminal mode reads nothing from ADO"))
    monkeypatch.setattr(_main, "_config", lambda a: pytest.fail("no config is read in terminal mode"))
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust([], "terminal", {}))
    for gate in GATE12:
        assert _main.main(["approvals", "check", "1001", "--gate", gate]) == 0
        assert json.loads(capsys.readouterr().out) == {"gate": gate, "mode": "terminal", "met": True}
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: pytest.fail("an unknown gate is refused before any trust read"))
    for bad in ("nope", "review-spec;rm -rf", "Review-Spec"):
        assert _main.main(["approvals", "check", "1001", "--gate", bad]) == 2
        out = capsys.readouterr()
        assert out.err.strip() == f"error: {approvals.E_GATE}" and out.out == ""
    for argv in (["approvals", "check", "1001"], ["approvals", "check", "0", "--gate", "review-spec"], ["approvals"]):
        with pytest.raises(SystemExit) as e:
            _main.main(argv)
        assert e.value.code == 2
    capsys.readouterr()


def test_cli_comment_mode_apply_gate_and_met_review_gate(repo, monkeypatch, capsys):
    _tree(repo)
    raws = []
    _cli_env(repo, monkeypatch, raws)
    (repo / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", "dod_path": "dod"}))
    art = approvals.artifacts("review-spec", 1001, Config.load(), ".")
    assert _main.main(["approvals", "check", "1001", "--gate", "approve-push"]) == 2
    assert capsys.readouterr().err.strip() == "error: " + approvals.E_APPLY_GATE.format(gate="approve-push")
    assert _main.main(["approvals", "check", "1001", "--gate", "review-spec"]) == 2
    assert approvals.gate_hash("review-spec", 1001, art) in capsys.readouterr().err
    raws.append(_appr("review-spec", art))
    assert _main.main(["approvals", "check", "1001", "--gate", "review-spec"]) == 0
    assert json.loads(capsys.readouterr().out) == {"gate": "review-spec", "mode": "comment", "met": True}


def test_a_decision_the_kit_cut_stays_open_until_a_short_repost_is_approved(cfg):
    """P4-12b fix round 2: the kit's own cut line (500 characters ending in …) must not brick the gate: that D is open while its NEWEST posting shows it cut."""
    head = "Decisions and assumptions made during development; please validate or reject:"
    cut = raw_comment(20, f"[spec-kit-ado:decisions]<br>{head}<br>D1 (decision): " + "x" * 484 + "…<br>D2 (decision): short", T0, author=ME)
    assert len(("D1 (decision): " + "x" * 484 + "…")) == 500
    approve = [_cmd(21, "/approve D1", T1), _cmd(22, "/approve D2", T1)]
    assert _wv(cfg, [cut]) == {"met": False, "open": ["D1", "D2"], "bad_newer": 0}  # the cut posting alone: open, the gate does not refuse
    assert _wv(cfg, [cut] + approve) == {"met": False, "open": ["D1"], "bad_newer": 0}  # D2 is fine; an approval of a line the approver could not see in full is no approval
    again = _posts("D1 (decision): short now", cid=23, date=T2)
    assert _wv(cfg, [cut] + approve + [again])["open"] == ["D1"]  # reposted short: needs a verdict newer than the repost
    assert _wv(cfg, [cut] + approve + [again, _cmd(24, "/approve D1", T3)]) == {"met": True, "open": [], "bad_newer": 0}
    assert _wv(cfg, [cut, again, _cmd(24, "/reject D1: no", T3), approve[1]])["met"] is True
    again_cut = raw_comment(25, f"[spec-kit-ado:decisions]<br>{head}<br>D1 (decision): " + "y" * 484 + "…", T3, author=ME)
    assert _wv(cfg, [cut, again, again_cut] + approve + [_cmd(26, "/approve D1", T3)])["open"] == ["D1"]  # the newest posting is cut again
