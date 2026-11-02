# F2 (P4-20 slice A, I-2): one "newest posting" rule for `decisions --sync`, the `wait-po-validation` gate and the done-time cross-check.
# An approval counts only for the line of the NEWEST decisions posting that lists that D id; a file line set back to an older posting never counts.
import pytest

from ado import approvals, decisions, strict
from tests.conftest import FakeClient, RouteClient, raw_comment, versions_response
from tests.test_approvals import HUMAN, KIT, ME, T0, T1, T2, T3, T12
from tests.test_decisions import S1, _file, _rows

X, Y = S1, S1.replace("cursor paging", "offset paging")
EXCLUDED = {ME.lower(), KIT}
OPEN = "open D1"
REFUSED = "refused: " + approvals.E_DECISIONS


def _md(line, cid):
    md = _file(line)
    return decisions._marked(md, decisions.plan_post(None, 1001, decisions.parse_decisions(md))["hashes"], cid)


def _html(line):
    return decisions.plan_post(None, 1001, decisions.parse_decisions(_file(line)))["html"]


def _post(line, cid, date, **kw):
    return raw_comment(cid, _html(line), date, author=kw.pop("author", ME), **kw)


def _vote(cid=30, text="/approve D1", date=T3):
    return raw_comment(cid, f"<p>{text}</p>", date, author=HUMAN)


def _paths(cfg, md, raws):
    """(sync verdict, wait-gate verdict, cross-check rules) of one file text over one story: the three paths."""
    rows = _rows(cfg, raws)
    new, rep = decisions.sync(md, rows, [HUMAN], EXCLUDED, FakeClient(cfg), 1001)
    try:
        st = approvals.wait_state(RouteClient(cfg, raws, me=ME), 1001, "wait-po-validation", T12, decisions_md=md)
        wait = "met" if st["met"] else "open " + ",".join(st["open"])
    except approvals.ApprovalMissing as e:  # a refusal is not an open id
        wait = "refused: " + str(e)
    xc = [f["rule"].rsplit("-", 1)[1] for f in strict.xcheck(new, rows, False, [HUMAN], EXCLUDED, FakeClient(cfg), 1001)]
    return rep["skipped"].get("D1", "applied"), wait, xc


LONG = "- [ ] D1: " + "x" * 600 + " → _(open)_ · kind: decision · why: w"
EDITED = raw_comment(10, _html(X), T0, author=ME, modified_by=HUMAN)
MATRIX = {  # name: (file text, comments) -> (sync, wait gate, cross-check)
    "unchanged": (_md(X, 10), [_post(X, 10, T0), _vote(date=T2)], ("applied", "met", [])),
    "reworded+repost, approve the new text": (_md(Y, 20), [_post(X, 10, T0), _post(Y, 20, T1), _vote()], ("applied", "met", [])),
    "repost+revert": (_md(X, 10), [_post(X, 10, T0), _post(Y, 20, T1), _vote()], ("posting-superseded", OPEN, ["dropped"])),
    "old text under the newest marker": (_md(X, 10).replace("posted: 10", "posted: 20"), [_post(X, 10, T0), _post(Y, 20, T1), _vote()],
                                         ("posting-mismatch", OPEN, ["marker"])),
    "edited posting": (_md(X, 10), [EDITED, _vote(date=T2)], ("posting-edited", REFUSED, ["marker"])),
    "repost edited, then revert": (_md(X, 10), [_post(X, 10, T0), _post(Y, 20, T1, modified_by=HUMAN), _vote()],  # an edited newer posting still counts as the newest
                                   ("posting-superseded", REFUSED, ["marker", "dropped"])),
    "cut line": (_md(LONG, 10), [_post(LONG, 10, T0), _vote(date=T2)], ("posting-lossy", OPEN, ["marker"])),
    "missing posting": (_md(X, 99), [_post(X, 10, T0), _vote(date=T2)], ("posting-missing", OPEN, ["marker"])),
}


@pytest.mark.parametrize("name", MATRIX)
def test_three_paths_agree_on_matrix(cfg, name):
    md, raws, want = MATRIX[name]
    got = _paths(cfg, md, raws)
    assert got == want  # exact: a refusal and an open id are different outcomes
    assert (got[0] == "applied") == (got[1] == "met") == (got[2] == [])  # one verdict, three readers


REVERT = (_md(X, 10), [_post(X, 10, T0), _post(Y, 20, T1), _vote()])


def test_reworded_repost_then_revert_not_validated_by_sync(cfg):
    md, raws = REVERT
    new, rep = decisions.sync(md, _rows(cfg, raws), [HUMAN], EXCLUDED, FakeClient(cfg), 1001)
    assert rep["applied"] == [] and rep["skipped"] == {"D1": "posting-superseded"} and new == md
    assert "posting-superseded" in decisions.SYNC_RULES


def test_reworded_repost_then_revert_wait_gate_open(cfg):
    md, raws = REVERT
    client = RouteClient(cfg, raws, me=ME)
    assert approvals.wait_state(client, 1001, "wait-po-validation", T12) == {"met": True, "open": [], "bad_newer": 0}  # ADO alone: the approver saw Y
    assert approvals.wait_state(client, 1001, "wait-po-validation", T12, decisions_md=md) == {"met": False, "open": ["D1"], "bad_newer": 0}
    assert all(c["method"] == "GET" for c in client.calls)


def test_reworded_repost_then_revert_xcheck_finding(cfg):
    md, raws = REVERT
    out = strict.xcheck(md, _rows(cfg, raws), False, [HUMAN], EXCLUDED, FakeClient(cfg), 1001)
    assert [f["rule"] for f in out] == ["decisions-xcheck-dropped"] and "D1" in out[0]["detail"]


def test_honest_repost_then_approve_new_text_passes_all_three(cfg):
    md, raws, _ = MATRIX["reworded+repost, approve the new text"]
    assert _paths(cfg, md, raws) == ("applied", "met", [])
    assert _paths(cfg, _md(X, 10), [_post(X, 10, T0), _vote(date=T2)])[0] == "applied"  # one posting, never superseded


def test_a_wait_gate_ignores_unmarked_lines_and_an_unreadable_file_is_refused(cfg, tmp_path, monkeypatch):
    md, raws = REVERT
    client = RouteClient(cfg, raws, me=ME)
    assert approvals.wait_state(client, 1001, "wait-po-validation", T12, decisions_md=_file(X))["met"] is True  # no marker: only a marker is a claim to check
    (tmp_path / "dod").mkdir()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dod" / "Decisions_1001.md").write_bytes(b"\xff\xfe")
    with pytest.raises(approvals.ApprovalMissing) as e:
        approvals.check(client, T12, 1001, "wait-po-validation", cfg)
    assert str(e.value) == approvals.E_DECISIONS_FILE
    (tmp_path / "dod" / "Decisions_1001.md").write_text(md)
    with pytest.raises(approvals.ApprovalMissing) as e:
        approvals.check(client, T12, 1001, "wait-po-validation", cfg)
    assert str(e.value) == approvals.E_WAIT.format(gate="wait-po-validation", story=1001, n=1, ids="D1", bad="")
    (tmp_path / "dod" / "Decisions_1001.md").unlink()
    assert approvals.check(client, T12, 1001, "wait-po-validation", cfg)["met"] is True  # no file: today's behaviour


def test_bypass_refusal_comes_before_the_decisions_file_read(cfg, tmp_path, monkeypatch):
    md, raws = REVERT
    (tmp_path / "dod").mkdir()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dod" / "Decisions_1001.md").write_bytes(b"\xff\xfe")
    with pytest.raises(approvals.ApprovalRefused):  # the token identity can bypass rules: that wins over the unreadable file
        approvals.check(RouteClient(cfg, raws, me=ME, bypass=True), T12, 1001, "wait-po-validation", cfg)
    seen = []
    approvals.wait_state(RouteClient(cfg, raws, me=ME), 1001, "wait-po-answers", T12, decisions_md=lambda: seen.append(1))
    assert seen == []  # the file is read for wait-po-validation only


def _reference_superseded(d, rows):
    """The per-line reading the index replaced (F2 first version): re-parse every marked comment for this one id."""
    from ado import approvals as ap, comments
    n = int(d["id"][1:])
    best, key, shown = None, None, ()
    for c in rows:
        k = ap._order(c)
        if k is None or (key is not None and k <= key) or not comments.marked(c.get("text"), decisions.MARKER):
            continue
        lines = tuple(" ".join(ln.split()) for ln in comments.kit_lines(str(c.get("html") or ""), decisions.MARKER)
                      if (m := decisions.POSTED_LINE.match(ln)) and int(m.group(1)) == n)
        if lines:
            best, key, shown = c, k, lines
    return bool(d["posted"] and best is not None and (str(d["posted"]["comment_id"]) != str(best["id"])
                                                      or " ".join(decisions._comment_line(d)[0].split()) not in shown))


def test_index_gives_the_same_answers_as_the_per_line_reading(cfg):
    z = S1.replace("cursor paging", "page cursors")
    two = "- [ ] D2: Cap at 50 rows → _(open)_ · kind: assumption · why: the story is silent"
    both = _html(X) .replace("D1 (decision): Use cursor paging", "D1 (decision): Use cursor paging<br>D2 (assumption): Cap at 50 rows — why: the story is silent")
    rows = _rows(cfg, [_post(X, 10, T0), _post(Y, 20, T1), raw_comment(25, both, "2026-10-01T10:30:00Z", author=HUMAN), _post(z, 30, T2, modified_by=HUMAN),
                       raw_comment(40, "<p>/approve D1</p>", T3, author=HUMAN), raw_comment(41, "[spec-kit-ado:decisions]<br><span>x</span>", T3, author=HUMAN)])
    lines = [_md(X, 10), _md(Y, 20), _md(X, 20), _md(z, 30), _md(X, 99), _file(X), _md(X + "\n" + two, 25)]
    for md in lines:
        index = decisions.posting_index(rows)
        for k in ("open", "validated", "rejected"):
            for d in decisions.parse_decisions(md)[k]:
                assert decisions.superseded(d, index) == _reference_superseded(d, rows), (md, d["id"])


def test_cost_is_linear_in_comment_bytes_not_decisions_times_comments(cfg, monkeypatch):
    import time
    from ado import comments
    calls, real = [], comments.kit_lines
    monkeypatch.setattr(comments, "kit_lines", lambda *a, **k: calls.append(1) or real(*a, **k))
    junk = "[spec-kit-ado:decisions]<br>" + "<br>".join(f"not a decision line {i} " + "x" * 90 for i in range(2000))  # ~200 KB, marked, no D lines
    lines = [f"- [ ] D{n}: Item {n} → _(open)_ · kind: decision · why: w" for n in range(1, 31)]
    md = _file("\n".join(lines))
    md = decisions._marked(md, decisions.plan_post(None, 1001, decisions.parse_decisions(md))["hashes"], 10)
    raws = [raw_comment(10, decisions.plan_post(None, 1001, decisions.parse_decisions(_file("\n".join(lines))))["html"], T0, author=ME)]
    raws += [raw_comment(100 + i, junk, T1, author=HUMAN) for i in range(20)]
    rows = _rows(cfg, raws)
    t = time.monotonic()
    new, rep = decisions.sync(md, rows, [HUMAN], EXCLUDED, FakeClient(cfg), 1001)
    strict.xcheck(new, rows, False, [HUMAN], EXCLUDED, FakeClient(cfg), 1001)
    # each comment is parsed a constant number of times, however many decisions are asked about (the per-line version made about 1340 calls here)
    assert rep["applied"] == [] and len(calls) < 10 * len(rows), (len(calls), len(rows))
    assert time.monotonic() - t < 60  # last-resort sanity bound only; the call count is the real check
