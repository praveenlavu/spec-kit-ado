"""F4 (P4-20 I-4): every comment is parsed once per run, however many targets are asked about, and a raw comment over the cap is never read."""
import time
from types import SimpleNamespace

from ado import approvals
from tests.conftest import FakeClient

HUMAN, OTHER, ME = "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666", "aaaa1111-0000-4000-8000-00000000000a"
T = "2026-10-01T10:00:0{}Z"


def row(cid, html, sec=0, author=HUMAN, **kw):
    return {"id": cid, "author": author, "modified_by": author, "version": 1, "on_behalf": False, "html": html, "date": T.format(sec), "modified": T.format(sec),
            "text": "", **kw}


def _count_parse(monkeypatch):
    calls = []
    real = approvals._parse
    monkeypatch.setattr(approvals, "_parse", lambda h: (calls.append(1), real(h))[1])
    return calls


def test_each_comment_parsed_once_for_many_targets(cfg, monkeypatch):
    rows = [row(i, f"/approve D{i % 30 + 1}", sec=i % 9, author=HUMAN if i % 2 else OTHER) for i in range(1, 41)]
    calls = _count_parse(monkeypatch)
    found = approvals.cached(rows)
    for n in range(1, 31):
        approvals.newest(FakeClient(cfg), 2, found, f"D{n}", [HUMAN], {ME})
    assert len(calls) == 40  # N, not 30 x N
    calls.clear()
    for n in range(1, 4):  # a plain list is parsed per target, as before (callers that care wrap it with `cached`)
        approvals.newest(FakeClient(cfg), 2, rows, f"D{n}", [HUMAN], {ME})
    assert len(calls) == 120
    calls.clear()  # `_decide` (3 gates, then a re-scan of the newer comments) reads each comment once
    monkeypatch.setattr(approvals, "comment_mode_allowed", lambda c: None)
    monkeypatch.setattr(approvals, "_load", lambda *a, **k: (list(rows), {ME}))
    approvals._decide(FakeClient(cfg), SimpleNamespace(approvers=[HUMAN]), 2, lambda: {g: "ab" * 32 for g in ("approve-a", "approve-b", "approve-c")})
    assert len(calls) == 40


def test_the_cache_follows_the_row_not_the_module(cfg, monkeypatch):
    rows = approvals.cached([row(1, "/approve D1")])
    calls = _count_parse(monkeypatch)
    approvals.newest(FakeClient(cfg), 2, rows, "D1", [HUMAN], set())
    rows[0]["html"] = "/reject D1"  # an edited row is read again
    assert approvals.newest(FakeClient(cfg), 2, rows, "D1", [HUMAN], set())["verb"] == "reject" and len(calls) == 2
    assert approvals.newest(FakeClient(cfg), 2, approvals.cached([row(1, "/approve D1")]), "D1", [HUMAN], set())["verb"] == "approve" and len(calls) == 3  # a new run


def test_oversized_comment_cannot_be_an_approval(cfg):
    cap = approvals.MAX_RAW_HTML
    pad = lambda n: "/approve D3<blockquote>" + "x" * (n - len("/approve D3<blockquote></blockquote>")) + "</blockquote>"  # noqa: E731
    assert len(pad(cap)) == cap
    ok = approvals.newest(FakeClient(cfg), 2, [row(1, pad(cap))], "D3", [HUMAN], set())
    assert ok and ok["verb"] == "approve"  # exactly the cap still reads (the quote is cut away)
    tally = {}
    assert approvals.newest(FakeClient(cfg), 2, [row(1, pad(cap + 1))], "D3", [HUMAN], set(), tally=tally) is None
    assert tally == {"bad": 1, "bad_newer": 1}  # named: an approver's unreadable line, counted for the caller to show (fixed text, a number)
    assert approvals.parse_command(pad(cap + 1)) is None
    # an earlier valid approval stands, with the oversized later line counted as a newer malformed one (it may be a retraction)
    got = approvals.newest(FakeClient(cfg), 2, [row(1, "/approve D3"), row(2, pad(cap + 1), sec=1)], "D3", [HUMAN], set())
    assert got["id"] == 1 and got["bad_newer"] == 1
    # a non-approver's is only counted as `bad`
    tally = {}
    assert approvals.newest(FakeClient(cfg), 2, [row(1, pad(cap + 1), author=OTHER)], "D3", [HUMAN], set(), tally=tally) is None and tally == {"bad": 1}


def test_oversized_non_approver_comment_leaves_an_approval_unchanged(cfg, monkeypatch):
    big = "x" * (approvals.MAX_RAW_HTML + 1)
    rows = [row(1, "/approve D3"), row(2, big, sec=5, author=OTHER), row(3, "/reject D3: no", sec=6, author=OTHER)]
    got = approvals.newest(FakeClient(cfg), 2, rows, "D3", [HUMAN], set())
    assert got["id"] == 1 and got["verb"] == "approve" and got["bad_newer"] == 0  # a later oversized comment by a non-approver is not a retraction
    h = "ab" * 32
    gate = [row(1, f"/approve approve-a {h}"), row(2, big, sec=5, author=OTHER)]
    monkeypatch.setattr(approvals, "comment_mode_allowed", lambda c: None)
    monkeypatch.setattr(approvals, "_load", lambda *a, **k: (list(gate), {ME}))
    verdict, bad, *_ = approvals._decide(FakeClient(cfg), SimpleNamespace(approvers=[HUMAN]), 2, lambda: {"approve-a": h})
    assert (verdict, bad) == ("approve", 0)  # the gate stays met
    # the same comment by the approver does cancel it (fail closed: it may be a retraction)
    gate[1] = row(2, big, sec=5)
    assert approvals._decide(FakeClient(cfg), SimpleNamespace(approvers=[HUMAN]), 2, lambda: {"approve-a": h})[:2] == (None, 1)


def test_oversized_non_approver_comment_does_not_stall(cfg):
    big = "<q>" * 350000  # ~1 MiB
    rows = approvals.cached([row(100 + i, big, sec=i % 9, author=OTHER) for i in range(20)])
    t = time.time()
    for n in range(1, 31):
        assert approvals.newest(FakeClient(cfg), 2, rows, f"D{n}", [HUMAN], {ME}) is None
    assert time.time() - t < 5.0


def _reference_newest(client, story_id, comments, target, approvers, excluded, tally=None):
    """The per-target parse as it was before F4 (a copy of `approvals.newest` that calls `_parse` itself for every comment)."""
    tally = {} if tally is None else tally
    count = lambda k, n=1: tally.__setitem__(k, tally.get(k, 0) + n)  # noqa: E731
    wanted, found, bads = {str(a).lower() for a in approvers}, [], []
    skip = {str(e).lower() for e in excluded}
    for c in comments:
        cmd, bad = approvals._parse(c.get("html")) if isinstance(c, dict) else (None, None)
        if bad is not None:
            count("bad")
            author = c["author"].lower() if isinstance(c.get("author"), str) else None
            if author in wanted and author not in skip and not c.get("on_behalf") and not approvals._elsewhere(bad, target):
                bads.append(approvals._order(c))
        if cmd is None or cmd["target"] != target:
            continue
        key = approvals._order(c)
        if key is None:
            count("date")
            continue
        found.append((key, cmd, c))
    out = None
    for key, cmd, c in sorted(found, key=lambda t: t[0], reverse=True):
        why = approvals.guard(client, story_id, c, approvers, excluded)
        if why is None:
            out = (key, {**cmd, "id": c["id"], "created": c["date"], "author": c["author"]})
            break
        count(why)
    newer = sum(1 for k in bads if k is None or out is None or k > out[0])
    if newer:
        count("bad_newer", newer)
    return None if out is None else {**out[1], "bad_newer": newer}


def test_results_identical_to_before_for_normal_comments(cfg):
    h = "ab" * 32
    rows = [row(1, "/approve D1"), row(2, "/reject D1: no", sec=1), row(3, "/approve D1", sec=2, author=OTHER), row(4, "<blockquote>/reject D2</blockquote>", sec=1),
            row(5, "/approve D2", sec=2), row(6, "/reject D2.", sec=3), row(7, f"/approve review-spec {h}", sec=1), row(8, "/answer Q1: yes", sec=1),
            row(9, "/answer Q1 oops", sec=2), row(10, "/approve D3", sec=1, on_behalf=True), row(11, "/approve D3", sec=2, author=ME), row(12, "/reject D3: x", sec=3, version=2),
            row(13, "<p>/approve D4</p><p>thanks</p>", sec=1), row(14, "/approve D4", sec=1, date="not a date"), row(15, "hello", sec=1), row(16, None, sec=1),
            row(17, "/approve D5" + " " * 70000, sec=1), row(18, "/reject D5", sec=2, author=None), row(19, "​", sec=2), row(20, "/approve D6: a@b.example", sec=1)]
    rows.append("not a row")
    targets = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "Q1", "review-spec", "review-plan"]
    cached = approvals.cached(rows)
    for t in targets:
        for approvers in ([HUMAN], [HUMAN, OTHER], [ME], []):
            t1, t2 = {}, {}
            want = _reference_newest(FakeClient(cfg), 2, rows, t, approvers, {ME}, tally=t1)
            assert approvals.newest(FakeClient(cfg), 2, cached, t, approvers, {ME}, tally=t2) == want, (t, approvers)
            assert t1 == t2, (t, approvers)


def test_sync_wait_gate_and_cross_check_parse_each_comment_once(cfg, monkeypatch):
    from ado import decisions, strict
    from tests.conftest import RouteClient
    from tests.test_approvals import T12
    from tests.test_decisions import _rows
    from tests.test_newest_posting import EXCLUDED, _md, _post, _vote, T0, T2
    from tests.test_decisions import S1, S2
    X = S1 + "\n" + S2 + "\n" + S2.replace("D2: Cap at 50 rows", "D3: Cap at 20 rows")
    md = _md(X, 10)
    raws = [_post(X, 10, T0)] + [_vote(cid=40 + i, text=f"/approve D{i % 3 + 1}", date=T2) for i in range(20)]
    calls = _count_parse(monkeypatch)
    rows = _rows(cfg, raws)
    new, _ = decisions.sync(md, rows, [HUMAN], EXCLUDED, FakeClient(cfg), 1001)
    assert len(calls) == len(raws)  # one parse per comment, not per decision
    calls.clear()
    strict.xcheck(new, rows, False, [HUMAN], EXCLUDED, FakeClient(cfg), 1001)
    assert len(calls) == len(raws)
    calls.clear()
    approvals.wait_state(RouteClient(cfg, raws, me=ME), 1001, "wait-po-validation", T12, decisions_md=md)
    assert len(calls) == len(raws)
