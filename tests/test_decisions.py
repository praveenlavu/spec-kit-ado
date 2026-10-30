import json
import os
import re

import pytest

from ado import decisions
from ado.__main__ import main
from ado.dod import SECTION2, render_dod, write_dod
from ado.config import Config
from tests.conftest import FakeClient

H = decisions.HEADING
OPEN = "- [ ] D1: Use cursor paging → _(open)_ · kind: decision · why: the story names no page size"
VAL = "- [x] D2: Cap at 50 rows → validated · by: PO · note: agreed in the thread"
REJ = "- [x] D3: Skip audit log → rejected · by: PO · note: not acceptable"


def _ledger(body, tail="## 3. AC\n"):
    return f"# DoD\n{SECTION2}\n- **Approach:** x\n{H}\n{body}\n{tail}"


def test_parse_open_validated_rejected():
    r = decisions.parse_decisions(_ledger("\n".join([OPEN, VAL, REJ])))
    assert r["unparsed"] is False
    assert r["open"] == [{"id": "D1", "kind": "decision", "text": "Use cursor paging",
                          "why": "the story names no page size", "status": "open", "by": "", "note": "",
                          "posted": None, "used": ""}]
    assert [d["id"] for d in r["validated"]] == ["D2"] and r["validated"][0]["by"] == "PO"
    assert r["validated"][0]["note"] == "agreed in the thread" and r["validated"][0]["status"] == "validated"
    assert [d["id"] for d in r["rejected"]] == ["D3"] and r["rejected"][0]["status"] == "rejected"


def test_round_trip_render_line():
    lines = [OPEN, VAL, REJ,
             OPEN + " · posted: 42:" + decisions.line_hash("Use cursor paging"),
             "- [ ] D4: Skip the freeze → _(open)_ · kind: override · why: outage · used: 2026-11-02",
             "- [x] D5: a → b → c → validated · by: PO · note: ok · posted: 7:deadbeef"]
    for line in lines:
        r = decisions.parse_decisions(_ledger(line))
        d = (r["open"] + r["validated"] + r["rejected"])[0]
        assert decisions.render_line(d) == line, line


def test_fenced_or_commented_lines_ignored():
    body = f"```\n{OPEN}\n```\n<!-- {VAL} -->\n<!--\n{REJ}\n-->\n- [ ] D9: real → _(open)_ · kind: assumption · why: w"
    r = decisions.parse_decisions(_ledger(body, "## 3. AC\n" + OPEN + "\n"))
    assert [d["id"] for d in r["open"]] == ["D9"] and not r["validated"] and not r["rejected"]
    assert r["unparsed"] is False
    assert decisions.parse_decisions(_ledger("<!-- never closed\n" + OPEN))["unparsed"] is True


def test_duplicate_id_first_wins():
    r = decisions.parse_decisions(_ledger(OPEN + "\n" + OPEN.replace("cursor paging", "offset paging") + "\n"
                                          + VAL.replace("D2", "D1")))
    assert [d["text"] for d in r["open"]] == ["Use cursor paging"] and r["validated"] == []


def test_malformed_line_sets_unparsed():
    for bad in ["- [ ] D1: no arrow", "- [ ] D1: t → _(open)_ · why: no kind",
                "- [ ] D1: t → _(open)_ · kind: guess · why: w", "- [ ] D1: t → validated · by: PO · note: n",
                "- [x] D1: t → _(open)_ · kind: decision · why: w", "- [x] D1: t → validated · note: no role",
                "- [ ] D1: t → _(open)_ · kind: decision · why: w · posted: nope"]:
        r = decisions.parse_decisions(_ledger(bad + "\n" + VAL))
        assert r["unparsed"] is True and r["open"] == [] and [d["id"] for d in r["validated"]] == ["D2"], bad
    assert decisions.parse_decisions("nothing") == {"open": [], "validated": [], "rejected": [], "unparsed": False}


def test_posted_marker_parsed_and_hash_changes_with_text():
    h = decisions.line_hash("Use cursor paging")
    assert len(h) == 8 and h == decisions.line_hash("Use  cursor\tpaging ") and h != decisions.line_hash("Use offset")
    r = decisions.parse_decisions(_ledger(f"{OPEN} · posted: 42:{h}"))
    assert r["open"][0]["posted"] == {"comment_id": 42, "hash": h}


def test_ledger_template_has_decisions_heading_in_section_2_only():
    md = render_dod({"id": 1001, "fields": {}}, "contoso", "Fabrikam", synced="2026-07-14")
    assert md.count(H) == 1
    s2, s3 = md.index(SECTION2), md.index("## 3.")
    assert s2 < md.index(H) < s3
    assert md.index("Open questions →") < md.index(H)
    assert "`- [ ] D1:" in md
    assert decisions.parse_decisions(md) == {"open": [], "validated": [], "rejected": [], "unparsed": False}


def _wi(ac):
    return {"id": 1001, "fields": {"Microsoft.VSTS.Common.AcceptanceCriteria": ac}}


def test_refresh_keeps_decision_lines(tmp_path):
    p = write_dod(_wi("old AC"), str(tmp_path), "contoso", "Fabrikam", synced="2026-07-14")
    p.write_text(p.read_text().replace("\n\n## 3.", "\n" + OPEN + "\n" + VAL + "\n\n## 3.", 1))
    before = p.read_text()
    write_dod(_wi("new AC"), str(tmp_path), "contoso", "Fabrikam", synced="2026-07-15", refresh=True)
    after = p.read_text()
    assert "new AC" in after and "old AC" not in after
    assert after[after.index(SECTION2):] == before[before.index(SECTION2):]
    assert [d["id"] for d in decisions.parse_decisions(after)["open"]] == ["D1"]


def test_cli_decisions_read_only_and_missing_ledger_exit_2(tmp_path, monkeypatch, capsys):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    assert main(["decisions", "1001"]) == 2
    assert "run `ado dod 1001` first" in capsys.readouterr().err
    (tmp_path / "dod").mkdir()
    led = tmp_path / "dod" / "DoD_1001.md"
    led.write_text(_ledger(OPEN + "\n" + VAL), encoding="utf-8")
    before = led.read_bytes()
    assert main(["decisions", "1001"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [d["id"] for d in out["open"]] == ["D1"] and [d["id"] for d in out["validated"]] == ["D2"]
    assert led.read_bytes() == before


def test_pathological_whitespace_line_parses_fast():
    import time
    status = "- [ ] D1: t → _(open)_"
    for bad in (status + " " * 200_000 + "· kind: decision · why: w", "- [ ] D1: " + "x" * 200_000 + " → _(open)_ · kind: decision · why: w"):
        t = time.perf_counter()
        r = decisions.parse_decisions(_ledger(bad + "\n" + VAL))
        assert time.perf_counter() - t < 1
        assert r["unparsed"] is True and r["open"] == [] and [d["id"] for d in r["validated"]] == ["D2"]


# ---- V3-3: post the unposted decision lines as one new comment (preview -> gate -> apply) ----
M = decisions.MARKER
HEAD = "Decisions and assumptions made during development; please validate or reject:"
H1 = decisions.line_hash("Use cursor paging")
H2 = decisions.line_hash("Retry twice")
OPEN2 = "- [ ] D4: Retry twice → _(open)_ · kind: assumption · why: the story is silent"


def _env(tmp_path, monkeypatch, body, posted_id=777):
    from ado import __main__ as m
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    (tmp_path / "dod").mkdir()
    led = tmp_path / "dod" / "DoD_1001.md"
    led.write_text(_ledger(body), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    fake = {}

    def make(cfg):
        fake["c"] = FakeClient(cfg, responses=[{"id": posted_id}])
        return fake["c"]
    monkeypatch.setattr(m, "AdoClient", make)
    return m, fake, led


def _writes(fake):
    return [c for c in fake["c"].calls if c["method"] != "GET"]


def _plan(ledger_body):
    return decisions.plan_post(None, 1001, decisions.parse_decisions(_ledger(ledger_body)))


def test_plan_none_when_everything_posted():
    posted = f"{OPEN} · posted: 5:{H1}"
    plan = _plan(posted)
    assert plan["action"] == "none" and plan["html"] == "" and plan["lines"] == []
    assert "nothing to post" in decisions.render_plan(1001, plan)


def test_plan_adds_one_new_comment_listing_unposted_lines_only():
    plan = _plan(f"{OPEN} · posted: 5:{H1}\n{OPEN2}\n{VAL}")
    assert plan["action"] == "add" and plan["lines"] == ["D4"] and plan["cut"] == []
    assert plan["html"] == f"{M}<br>{HEAD}<br>D4 (assumption): Retry twice — why: the story is silent"
    assert "D1" not in plan["html"]


def test_edited_line_after_posting_is_reposted():
    plan = _plan(f"- [ ] D1: Use cursor paging v2 → _(open)_ · kind: decision · why: w · posted: 5:{H1}")
    assert plan["lines"] == ["D1"] and "Use cursor paging v2" in plan["html"]
    assert plan["hashes"] == {"D1": decisions.line_hash("Use cursor paging v2")}


def test_resolved_lines_are_not_posted():
    assert _plan(VAL + "\n" + REJ)["action"] == "none"


def test_comment_never_contains_names_mentions_or_emails():
    # one behaviour: an address or @mention typed in a decision is replaced like `ado comments` does, never refused
    body = ("- [ ] D1: Ask @dana or d@contoso.example about <a href=\"#\" data-vss-mention=\"x\">Dana Fictional</a> "
            "→ _(open)_ · kind: decision · why: mail e@contoso.example <b>")
    html_ = _plan(body)["html"]
    assert "dana" not in html_.lower() and "contoso" not in html_ and "Fictional" not in html_
    assert html_.count("@user") == 2 and html_.count("[email]") == 2 and "&lt;b&gt;" in html_
    assert "<b>" not in html_


def test_long_line_is_cut_at_500_with_ellipsis():
    plan = _plan(f"- [ ] D1: {'x' * 600} → _(open)_ · kind: decision · why: w")
    line = plan["html"].split("<br>")[2]
    assert len(line) == 500 and line.endswith("…") and plan["cut"] == ["D1"]


def test_dry_run_and_plan_out_write_nothing(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    before = led.read_bytes()
    assert m.main(["decisions", "1001", "--post"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "add"
    assert m.main(["decisions", "1001", "--plan-out", "p.md"]) == 0
    plan = (tmp_path / "p.md").read_text()
    assert "ADD a new comment" in plan and f"> {M}" in plan and "> D1 (decision): Use cursor paging" in plan
    assert _writes(fake) == [] and led.read_bytes() == before


def test_apply_posts_exact_previewed_html_then_writes_marker(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, f"{OPEN}\n{VAL}\n{OPEN2} · posted: 4:{'0' * 8}")
    assert m.main(["decisions", "1001", "--post", "--plan-out", "p.md"]) == 0
    capsys.readouterr()
    assert m.main(["decisions", "1001", "--post", "--apply", "--expect-plan", "p.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    html_ = decisions.plan_post(None, 1001, decisions.parse_decisions(_ledger(f"{OPEN}\n{VAL}\n{OPEN2}")))["html"]
    assert _writes(fake) == [{"method": "POST", "body": {"text": html_}, "content_type": "application/json",
                              "path": "/Fabrikam/_apis/wit/workItems/1001/comments?api-version=7.1-preview.4"}]
    assert out["comment_id"] == 777 and out["lines"] == ["D1", "D4"]
    lines = led.read_text().splitlines()
    assert f"{OPEN} · posted: 777:{H1}" in lines and f"{OPEN2} · posted: 777:{H2}" in lines and VAL in lines
    assert led.read_text().count("posted:") == 2  # the old marker was replaced, not stacked
    d = decisions.parse_decisions(led.read_text())
    assert d["unparsed"] is False and d["open"][0]["posted"] == {"comment_id": 777, "hash": H1}
    assert m.main(["decisions", "1001", "--post", "--plan-out", "q.md"]) == 0  # a second preview: nothing left
    assert "nothing to post" in (tmp_path / "q.md").read_text()


def test_expect_plan_mismatch_exit_2_no_write(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    assert m.main(["decisions", "1001", "--post", "--plan-out", "p.md"]) == 0
    approved = (tmp_path / "p.md").read_text()
    led.write_text(_ledger(OPEN + "\n" + OPEN2), encoding="utf-8")
    before = led.read_bytes()
    assert m.main(["decisions", "1001", "--post", "--apply", "--expect-plan", "p.md"]) == 2
    assert "plan changed since approval" in capsys.readouterr().err
    assert _writes(fake) == [] and led.read_bytes() == before and (tmp_path / "p.md").read_text() == approved
    assert "D4" in (tmp_path / "p.md.new").read_text()


def test_apply_needs_post_and_expect_plan_needs_apply(capsys):
    for argv in (["decisions", "1001", "--apply"], ["decisions", "1001", "--post", "--expect-plan", "p.md"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2


def test_ledger_write_is_atomic_on_failure(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    before = led.read_bytes()

    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 2
    err = capsys.readouterr().err
    assert "comment 777 was posted" in err and f"D1: · posted: 777:{H1}" in err
    assert led.read_bytes() == before and list(led.parent.glob("*.tmp")) == []


def test_ledger_changed_since_plan_posts_nothing(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    plan = decisions.plan_post(None, 1001, decisions.parse_decisions(led.read_text()))
    led.write_text(_ledger(OPEN.replace("cursor", "offset")), encoding="utf-8")
    client = m.AdoClient(None)
    with pytest.raises(ValueError, match="changed since the plan"):
        decisions.apply_post(client, 1001, plan, led)
    assert _writes(fake) == []


def test_line_terminated_by_u2028_keeps_the_next_line_intact(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN + "\u2028" + VAL)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    md = led.read_text(encoding="utf-8")
    assert f"{OPEN} · posted: 777:{H1}\u2028{VAL}" in md
    d = decisions.parse_decisions(md)
    assert d["unparsed"] is False and [x["id"] for x in d["open"]] == ["D1"] and [x["id"] for x in d["validated"]] == ["D2"]


def test_crlf_ledger_round_trips(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN + "\n" + VAL)
    led.write_bytes(led.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    before = led.read_bytes()
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    after = led.read_bytes()
    assert after.count(b"\r\n") == before.count(b"\r\n") and after.count(b"\n") == before.count(b"\n")
    assert f"{OPEN} · posted: 777:{H1}\r\n{VAL}\r\n".encode("utf-8") in after


def test_marker_lookalike_in_the_text_stays_verbatim_and_is_posted_once(tmp_path, monkeypatch):
    text = "a · posted: 5:abcdef12 · b"
    line = f"- [ ] D1: {text} → _(open)_ · kind: decision · why: w"
    m, fake, led = _env(tmp_path, monkeypatch, line)
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    md = led.read_text(encoding="utf-8")
    assert f"{line} · posted: 777:{decisions.line_hash(text)}" in md
    assert decisions.parse_decisions(md)["open"][0]["text"] == text
    assert decisions.plan_post(None, 1001, decisions.parse_decisions(md))["action"] == "none"


def test_comment_line_strips_control_characters(tmp_path, monkeypatch):
    body = "- [ ] D1: hide\x1b[8mme\x00 now\x7fthere → _(open)_ · kind: decision · why: w"
    plan = _plan(body)
    assert not any(c in plan["html"] for c in "\x1b\x00\x7f") and "hide" in plan["html"]
    assert not any(ord(c) < 32 and c not in "\n" for c in decisions.render_plan(1001, plan))


def _client():
    return FakeClient(Config(organization="contoso", project="Fabrikam"), responses=[{"id": 777}])


def test_ledger_is_reread_after_the_post(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    plan = decisions.plan_post(None, 1001, decisions.parse_decisions(led.read_text()))
    client = _client()
    post = client.post
    client.post = lambda *a, **k: (led.write_text(led.read_text() + "- added while posting\n", encoding="utf-8"), post(*a, **k))[1]
    assert decisions.apply_post(client, 1001, plan, led) == {"comment_id": 777}
    md = led.read_text(encoding="utf-8")
    assert "added while posting" in md and f"posted: 777:{H1}" in md


def test_targeted_line_edited_during_the_post_is_not_marked(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    plan = decisions.plan_post(None, 1001, decisions.parse_decisions(led.read_text()))
    client = _client()
    post = client.post
    edited = _ledger(OPEN.replace("cursor", "offset"))
    client.post = lambda *a, **k: (led.write_text(edited, encoding="utf-8"), post(*a, **k))[1]
    with pytest.raises(ValueError, match=rf"comment 777 was posted.*D1: · posted: 777:{H1}"):
        decisions.apply_post(client, 1001, plan, led)
    assert led.read_text(encoding="utf-8") == edited


# ---- V3-4: `ado decisions <id> --resolve D<n> --validated|--rejected` (a human, in a terminal) ----
KEPT = "- [ ] D4: Wrap it → _(open)_ · kind: override · why: skip the rule · posted: 5:abcdef12 · used: 2026-01-01"


def _tty(monkeypatch, on=True):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: on)


def _resolve(m, *extra):
    return m.main(["decisions", "1001", "--resolve", *extra])


def test_resolve_validates_only_that_line(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, "\n".join([OPEN, VAL, REJ]))
    _tty(monkeypatch)
    before = led.read_text(encoding="utf-8").splitlines()
    assert _resolve(m, "D1", "--validated") == 0
    assert json.loads(capsys.readouterr().out)["resolved"] == "D1"
    after = led.read_text(encoding="utf-8").splitlines()
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(before) == len(after) and len(changed) == 1 and after[changed[0]].startswith("- [x] D1:")
    d = decisions.parse_decisions(led.read_text(encoding="utf-8"))
    assert d["unparsed"] is False and [x["id"] for x in d["validated"]] == ["D1", "D2"] and d["open"] == []
    assert d["validated"][0]["by"] == "PO" and d["validated"][0]["note"] == ""
    assert fake == {} and list(led.parent.glob("*.tmp")) == []  # no client was built: no request


def test_resolve_rejected_then_validated_allowed(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, "\n".join([OPEN, REJ]))
    _tty(monkeypatch)
    assert _resolve(m, "D1", "--rejected") == 0
    assert _resolve(m, "D3", "--validated", "--note", "changed my mind") == 2  # already resolved: needs --force
    assert _resolve(m, "D3", "--validated", "--note", "changed my mind", "--force") == 0
    d = decisions.parse_decisions(led.read_text(encoding="utf-8"))
    assert [x["id"] for x in d["rejected"]] == ["D1"] and [x["id"] for x in d["validated"]] == ["D3"]
    assert d["validated"][0]["note"] == "changed my mind"


def test_refuses_without_tty(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch, False)
    before = led.read_bytes()
    assert _resolve(m, "D1", "--validated") == 2
    assert "own terminal" in capsys.readouterr().err and led.read_bytes() == before
    led.unlink()  # checked before the ledger is even looked for
    assert _resolve(m, "D1", "--validated") == 2
    err = capsys.readouterr().err
    assert "own terminal" in err and "ado dod" not in err


def test_unknown_id_exit_2(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch)
    before = led.read_bytes()
    for bad in ("D9", "1", "d1", "D1x"):
        assert _resolve(m, bad, "--validated") == 2
        assert "no decision" in capsys.readouterr().err and led.read_bytes() == before


def test_by_and_note_validated(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch)
    before = led.read_bytes()
    bad = [("--by", "Dana; PO"), ("--by", "x" * 41), ("--by", ""), ("--by", "P\nO"), ("--by", "P O"), ("--by", "PO\x1b"),
           ("--note", "n" * 301), ("--note", "a\nb"), ("--note", "a\rb"), ("--note", "a b"), ("--note", "a b"),
           ("--note", "a\x1b[8mb"), ("--note", "a\x00b"), ("--note", "a\x85b"), ("--note", "a\x7fb"),
           ("--note", "x · kind: assumption"), ("--note", "x → rejected")]
    for flag, val in bad:
        assert _resolve(m, "D1", "--validated", flag, val) == 2, (flag, val)
        assert capsys.readouterr().err.startswith("error:") and led.read_bytes() == before, (flag, val)
    assert _resolve(m, "D1", "--validated", "--by", "Product Owner 2", "--note", "n" * 300) == 0
    d = decisions.parse_decisions(led.read_text(encoding="utf-8"))["validated"][0]
    assert d["by"] == "Product Owner 2" and d["note"] == "n" * 300


def test_resolve_keeps_posted_marker_and_other_markers(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, KEPT + "\n" + VAL)
    _tty(monkeypatch)
    old = decisions.parse_decisions(led.read_text(encoding="utf-8"))["open"][0]
    assert _resolve(m, "D4", "--rejected", "--note", "no") == 0
    md = led.read_text(encoding="utf-8")
    line = next(x for x in md.splitlines() if "D4:" in x)
    assert line == "- [x] D4: Wrap it → rejected · by: PO · note: no · kind: override · why: skip the rule · posted: 5:abcdef12 · used: 2026-01-01"
    new = decisions.parse_decisions(md)["rejected"][0]
    assert decisions.render_line(new) == line
    assert {k: new[k] for k in ("id", "kind", "text", "why", "posted", "used")} == {k: old[k] for k in ("id", "kind", "text", "why", "posted", "used")}


def test_resolve_keeps_line_terminators(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN + " " + VAL)
    _tty(monkeypatch)
    assert _resolve(m, "D1", "--validated") == 0
    md = led.read_text(encoding="utf-8")
    assert " - [x] D2:" in md and decisions.parse_decisions(md)["unparsed"] is False
    led.write_bytes(_ledger(OPEN + "\n" + VAL).replace("\n", "\r\n").encode("utf-8"))
    before = led.read_bytes()
    assert _resolve(m, "D2", "--rejected", "--by", "PO", "--force") == 0
    after = led.read_bytes()
    assert after.count(b"\r\n") == before.count(b"\r\n") == after.count(b"\n")
    assert [x["id"] for x in decisions.parse_decisions(after.decode("utf-8"))["rejected"]] == ["D2"]


def test_resolved_line_over_max_line_is_refused(tmp_path, monkeypatch, capsys):
    long_open = f"- [ ] D1: {'t' * 1800} → _(open)_ · kind: decision · why: w"
    m, fake, led = _env(tmp_path, monkeypatch, long_open)
    _tty(monkeypatch)
    before = led.read_bytes()
    assert _resolve(m, "D1", "--validated", "--note", "n" * 250) == 2
    assert str(decisions.MAX_LINE) in capsys.readouterr().err and led.read_bytes() == before


def test_resolve_flag_combinations_exit_2(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch)
    before = led.read_bytes()
    for argv in (["--resolve", "D1", "--validated", "--post"], ["--resolve", "D1", "--validated", "--apply"],
                 ["--resolve", "D1", "--validated", "--plan-out", "p.md"],
                 ["--resolve", "D1", "--validated", "--expect-plan", "p.md"],
                 ["--resolve", "D1"], ["--resolve", "D1", "--validated", "--rejected"],
                 ["--validated"], ["--by", "PO"], ["--note", "x"], ["--force"]):
        with pytest.raises(SystemExit) as e:
            m.main(["decisions", "1001", *argv])
        assert e.value.code == 2, argv
    assert led.read_bytes() == before and not (tmp_path / "p.md").exists()


def test_resolve_write_is_atomic_and_refuses_a_ledger_changed_meanwhile(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch)
    before = led.read_bytes()
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert _resolve(m, "D1", "--validated") == 2
    assert "disk full" in capsys.readouterr().err and led.read_bytes() == before and list(led.parent.glob("*.tmp")) == []
    monkeypatch.setattr(os, "replace", real)
    pure = decisions.resolve
    monkeypatch.setattr(decisions, "resolve", lambda *a: (led.write_text(led.read_text() + "edited\n"), pure(*a))[1])
    assert _resolve(m, "D1", "--validated") == 2
    assert "changed" in capsys.readouterr().err and "edited" in led.read_text() and "[ ] D1" in led.read_text()


# ---- V3-4 fix round 1 ----
def test_note_with_comment_or_fence_markers_is_refused(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN + "\n" + OPEN2)
    _tty(monkeypatch)
    before = led.read_bytes()
    for note in ("x <!-- y", "x --> y", "<!--", "a ``` b", "~~~", "x ````"):
        assert _resolve(m, "D1", "--validated", "--note", note) == 2, note
        assert capsys.readouterr().err.startswith("error:") and led.read_bytes() == before, note


def test_whole_ledger_is_verified_before_writing(monkeypatch):
    md = _ledger(OPEN + "\n" + OPEN2)
    monkeypatch.setattr(decisions, "_MARKUP", re.compile("(?!)"))  # as if the note check missed `<!--`
    with pytest.raises(ValueError, match="D1"):  # the line alone reads back, the ledger does not
        decisions.resolve(md, "D1", "validated", "PO", "x <!-- y")
    assert "- [x] D1:" in decisions.resolve(md, "D1", "validated", "PO", "fine")  # the happy path still works


def test_inline_comment_in_the_target_line_is_refused(tmp_path, monkeypatch, capsys):
    line = "- [ ] D1: Use <!-- hidden --> paging → _(open)_ · kind: decision · why: w"
    m, fake, led = _env(tmp_path, monkeypatch, line + "\n" + OPEN2)
    _tty(monkeypatch)
    before = led.read_bytes()
    assert _resolve(m, "D1", "--validated") == 2
    assert "D1" in capsys.readouterr().err and led.read_bytes() == before
    assert _resolve(m, "D4", "--validated") == 0  # a comment in another line does not matter


def test_re_resolving_needs_force(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, VAL)
    _tty(monkeypatch)
    before = led.read_bytes()
    assert _resolve(m, "D2", "--rejected") == 2
    assert "--force" in capsys.readouterr().err and led.read_bytes() == before
    assert _resolve(m, "D2", "--rejected", "--force") == 0
    assert decisions.parse_decisions(led.read_text(encoding="utf-8"))["rejected"][0]["id"] == "D2"


def test_empty_note_leaves_no_double_space(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, KEPT)
    _tty(monkeypatch)
    assert _resolve(m, "D4", "--validated") == 0
    line = next(x for x in led.read_text(encoding="utf-8").splitlines() if "D4:" in x)
    assert "  " not in line
    assert decisions.render_line(decisions.parse_decisions(led.read_text(encoding="utf-8"))["validated"][0]) == line


def test_temp_file_is_unique_and_the_mode_is_kept(tmp_path, monkeypatch):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    _tty(monkeypatch)
    led.chmod(0o640)
    other = led.with_name(led.name + ".tmp")
    other.write_text("not mine", encoding="utf-8")
    assert _resolve(m, "D1", "--validated") == 0
    assert other.read_text(encoding="utf-8") == "not mine" and (led.stat().st_mode & 0o777) == 0o640
    other.unlink()
    assert list(led.parent.glob("*.tmp")) == []


def test_has_decision_lines():
    assert decisions.has_decision_lines(_ledger(OPEN)) and decisions.has_decision_lines(_ledger(VAL))
    assert decisions.has_decision_lines(_ledger("- [ ] D4 not in the grammar"))  # malformed still counts
    assert not decisions.has_decision_lines(_ledger("- [ ] Q1: which? → _(open)_ · owner: lead"))
    assert not decisions.has_decision_lines(_ledger("```\n" + OPEN + "\n```"))  # a fenced example is not a line
    assert not decisions.has_decision_lines(_ledger("<!-- " + OPEN + " -->"))
    assert not decisions.has_decision_lines("# DoD\nno section 2\n")
