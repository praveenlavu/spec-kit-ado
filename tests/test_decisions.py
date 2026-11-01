import json
import os
import re
from pathlib import Path

import pytest

from ado import decisions
from ado.__main__ import main
from ado.dod import render_dod, write_dod
from ado.config import Config
from tests.conftest import FakeClient

OPEN = "- [ ] D1: Use cursor paging → _(open)_ · kind: decision · why: the story names no page size"
VAL = "- [x] D2: Cap at 50 rows → validated · by: PO · note: agreed in the thread"
REJ = "- [x] D3: Skip audit log → rejected · by: PO · note: not acceptable"


def _file(body):
    """A decisions file: the template (title and format note) followed by the entries."""
    return decisions.render_file(1001) + body + "\n"


def test_parse_open_validated_rejected():
    r = decisions.parse_decisions(_file("\n".join([OPEN, VAL, REJ])))
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
        r = decisions.parse_decisions(_file(line))
        d = (r["open"] + r["validated"] + r["rejected"])[0]
        assert decisions.render_line(d) == line, line


def test_parse_reads_whole_file_ignoring_fences():
    body = f"```\n{OPEN}\n```\n<!-- {VAL} -->\n<!--\n{REJ}\n-->\n- [ ] D9: real → _(open)_ · kind: assumption · why: w"
    r = decisions.parse_decisions(_file(body))
    assert [d["id"] for d in r["open"]] == ["D9"] and not r["validated"] and not r["rejected"]
    assert r["unparsed"] is False
    assert decisions.parse_decisions(_file("<!-- never closed\n" + OPEN))["unparsed"] is True
    # no `## ` heading is needed or looked for: lines after any heading are read too
    r = decisions.parse_decisions("# Decisions\n## Later\n" + OPEN + "\n## 3. x\n" + VAL + "\n")
    assert [d["id"] for d in r["open"]] == ["D1"] and [d["id"] for d in r["validated"]] == ["D2"]


def test_duplicate_id_first_wins():
    r = decisions.parse_decisions(_file(OPEN + "\n" + OPEN.replace("cursor paging", "offset paging") + "\n"
                                          + VAL.replace("D2", "D1")))
    assert [d["text"] for d in r["open"]] == ["Use cursor paging"] and r["validated"] == []


def test_malformed_line_sets_unparsed():
    for bad in ["- [ ] D1: no arrow", "- [ ] D1: t → _(open)_ · why: no kind",
                "- [ ] D1: t → _(open)_ · kind: guess · why: w", "- [ ] D1: t → validated · by: PO · note: n",
                "- [x] D1: t → _(open)_ · kind: decision · why: w", "- [x] D1: t → validated · note: no role",
                "- [ ] D1: t → _(open)_ · kind: decision · why: w · posted: nope"]:
        r = decisions.parse_decisions(_file(bad + "\n" + VAL))
        assert r["unparsed"] is True and r["open"] == [] and [d["id"] for d in r["validated"]] == ["D2"], bad
    assert decisions.parse_decisions("nothing") == {"open": [], "validated": [], "rejected": [], "unparsed": False}


def test_posted_marker_parsed_and_hash_changes_with_text():
    h = decisions.line_hash("Use cursor paging")
    assert len(h) == 8 and h == decisions.line_hash("Use  cursor\tpaging ") and h != decisions.line_hash("Use offset")
    r = decisions.parse_decisions(_file(f"{OPEN} · posted: 42:{h}"))
    assert r["open"][0]["posted"] == {"comment_id": 42, "hash": h}


def test_dod_template_no_longer_seeds_decisions_heading_and_points_to_file():
    md = render_dod({"id": 1001, "fields": {}}, "contoso", "Fabrikam", synced="2026-07-14")
    assert "Decisions & assumptions" not in md and "D1:" not in md
    bullet = next(x for x in md.splitlines() if x.startswith("- **Assumptions:**"))
    assert "Decisions_1001.md" in bullet and "`ado decisions 1001 --add`" in bullet
    assert md.count("Decisions_1001.md") == 1
    assert decisions.parse_decisions(md) == {"open": [], "validated": [], "rejected": [], "unparsed": False}


def test_file_template_has_title_and_format_note_that_does_not_parse():
    md = decisions.render_file(1001)
    assert md.startswith("# Decisions — US1001\n") and "<!--" not in md and "-->" not in md
    assert "`- [ ] D1: <text> → _(open)_ · kind: decision · why: <reason>`" in md
    assert decisions.parse_decisions(md) == {"open": [], "validated": [], "rejected": [], "unparsed": False}
    assert not decisions.has_decision_lines(md)
    assert decisions.decisions_file("dod", 1001) == Path("dod") / "Decisions_1001.md"


def _wi(ac):
    return {"id": 1001, "fields": {"Microsoft.VSTS.Common.AcceptanceCriteria": ac}}


def test_refresh_neither_touches_nor_requires_the_decisions_file(tmp_path):
    p = write_dod(_wi("old AC"), str(tmp_path), "contoso", "Fabrikam", synced="2026-07-14")
    write_dod(_wi("new AC"), str(tmp_path), "contoso", "Fabrikam", synced="2026-07-15", refresh=True)
    assert "new AC" in p.read_text() and not (tmp_path / "Decisions_1001.md").exists()
    d = tmp_path / "Decisions_1001.md"
    d.write_text(_file(OPEN), encoding="utf-8")
    before = (d.read_bytes(), d.stat().st_mtime_ns)
    write_dod(_wi("newer AC"), str(tmp_path), "contoso", "Fabrikam", synced="2026-07-16", refresh=True)
    assert (d.read_bytes(), d.stat().st_mtime_ns) == before and "newer AC" in p.read_text()


def test_cli_decisions_read_only_and_missing_file_exit_2(tmp_path, monkeypatch, capsys):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    assert main(["decisions", "1001"]) == 2
    assert "`ado decisions 1001 --add " in capsys.readouterr().err
    (tmp_path / "dod").mkdir()
    led = tmp_path / "dod" / "Decisions_1001.md"
    led.write_text(_file(OPEN + "\n" + VAL), encoding="utf-8")
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
        r = decisions.parse_decisions(_file(bad + "\n" + VAL))
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
    led = tmp_path / "dod" / "Decisions_1001.md"
    led.write_text(_file(body), encoding="utf-8")
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
    return decisions.plan_post(None, 1001, decisions.parse_decisions(_file(ledger_body)))


def test_plan_none_when_everything_posted():
    posted = f"{OPEN} · posted: 5:{H1}"
    plan = _plan(posted)
    assert plan["action"] == "none" and plan["html"] == "" and plan["lines"] == []
    assert "nothing to post" in decisions.render_plan(1001, plan)


def test_preview_says_gets_for_one_line_and_get_for_several():
    one = decisions.render_plan(1001, _plan(OPEN))
    assert "After posting, D1 gets ` · posted: <comment id>:<hash>` in the decisions file." in one
    two = decisions.render_plan(1001, _plan(OPEN + "\n- [ ] D2: two → _(open)_ · kind: decision · why: w"))
    assert "After posting, D1, D2 get ` · posted:" in two


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
    html_ = decisions.plan_post(None, 1001, decisions.parse_decisions(_file(f"{OPEN}\n{VAL}\n{OPEN2}")))["html"]
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
    led.write_text(_file(OPEN + "\n" + OPEN2), encoding="utf-8")
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
    led.write_text(_file(OPEN.replace("cursor", "offset")), encoding="utf-8")
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
    edited = _file(OPEN.replace("cursor", "offset"))
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
    led.write_bytes(_file(OPEN + "\n" + VAL).replace("\n", "\r\n").encode("utf-8"))
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
    md = _file(OPEN + "\n" + OPEN2)
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
    assert decisions.has_decision_lines(_file(OPEN)) and decisions.has_decision_lines(_file(VAL))
    assert decisions.has_decision_lines(_file("- [ ] D4 not in the grammar"))  # malformed still counts
    assert not decisions.has_decision_lines(_file("- [ ] Q1: which? → _(open)_ · owner: lead"))
    assert not decisions.has_decision_lines(_file("```\n" + OPEN + "\n```"))  # a fenced example is not a line
    assert not decisions.has_decision_lines(_file("<!-- " + OPEN + " -->"))
    assert not decisions.has_decision_lines("# DoD\nno section 2\n")


# ---- V3-5b: the decisions live in <dod_path>/Decisions_<id>.md; `--add` writes them ----
def _add_env(tmp_path, monkeypatch):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.delenv("ADO_TOKEN", raising=False)  # a local write: no token, no request
    return tmp_path / "dod" / "Decisions_1001.md"


def _add(*extra, text="Use cursor paging", kind="decision"):
    return main(["decisions", "1001", "--add", text, "--kind", kind, *extra])


def test_add_creates_file_and_appends_next_id(tmp_path, monkeypatch, capsys):
    f = _add_env(tmp_path, monkeypatch)
    assert _add("--why", "the story names no page size") == 0
    assert json.loads(capsys.readouterr().out) == {"id": "D1", "file": str(Path("dod") / "Decisions_1001.md")}
    assert f.read_text(encoding="utf-8") == decisions.render_file(1001) + OPEN + "\n"
    # next id = max existing + 1, even with gaps, a resolved line and a duplicate id
    f.write_text(decisions.render_file(1001) + "\n".join([OPEN, VAL.replace("D2", "D7"), OPEN.replace("paging", "x")]) + "\n")
    before = f.read_text()
    assert _add("--why", "w", text="Retry twice", kind="assumption") == 0
    assert json.loads(capsys.readouterr().out)["id"] == "D8"
    after = f.read_text()
    assert after == before + "- [ ] D8: Retry twice → _(open)_ · kind: assumption · why: w\n"
    d = decisions.parse_decisions(after)
    assert d["unparsed"] is False and [x["id"] for x in d["open"]][-1] == "D8"
    assert list(f.parent.glob("*.tmp")) == []


def test_add_defaults_why_adds_a_missing_final_newline_and_keeps_crlf(tmp_path, monkeypatch):
    f = _add_env(tmp_path, monkeypatch)
    assert _add() == 0
    assert "· kind: decision · why: unspecified\n" in f.read_text()
    f.write_bytes((decisions.render_file(1001) + OPEN).replace("\n", "\r\n").encode("utf-8"))  # no final newline
    assert _add(text="b", kind="assumption") == 0
    raw = f.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") and b"D2: b" in raw
    assert decisions.parse_decisions(raw.decode("utf-8"))["unparsed"] is False


def test_add_numbers_after_a_malformed_line_too(tmp_path, monkeypatch):
    f = _add_env(tmp_path, monkeypatch)
    f.parent.mkdir()
    f.write_text(decisions.render_file(1001) + "- [ ] D5 not in the grammar\n", encoding="utf-8")
    assert _add() == 0
    assert "D6: Use cursor paging" in f.read_text()


def test_add_scrubs_mentions_and_emails_and_trims(tmp_path, monkeypatch):
    f = _add_env(tmp_path, monkeypatch)
    assert _add("--why", "ask @dana", text="  mail d@contoso.example <b>x</b> ") == 0
    line = f.read_text().splitlines()[-1]
    assert line == "- [ ] D1: mail [email] <b>x</b> → _(open)_ · kind: decision · why: ask @user"


def test_add_refuses_control_chars_comment_openers_and_long_text(tmp_path, monkeypatch, capsys):
    f = _add_env(tmp_path, monkeypatch)
    secret = "TOPSECRETTEXT"
    bad_text = ["a\nb", "a\rb", "a\tb", "a\x1bb", "a\x00b", "a\x7fb", "a\x85b", "a\u2028b", "a\u2029b",
                "x <!-- y", "x --> y", "a ``` b", "~~~", "x ````", "", "   ", "x" * 1501]
    for t in bad_text:
        assert _add(text=secret + t) == 2 if t.strip() else _add(text=t) == 2, repr(t)
        err = capsys.readouterr().err
        assert err.startswith("error:") and secret not in err and not f.exists(), repr(t)
    for why in ("a\nb", "a\x1bb", "w <!-- x", "w ```", "w" * 301, "w · kind: assumption"):
        assert _add("--why", why, text=secret) == 2, repr(why)
        err = capsys.readouterr().err
        assert err.startswith("error:") and secret not in err and "wwww" not in err and not f.exists(), repr(why)
    for kind in ("override", "Decision", "", "x"):
        assert _add(text=secret, kind=kind) == 2
        err = capsys.readouterr().err
        assert "decision or assumption" in err and secret not in err and not f.exists()
    assert _add(text="x" * 1500, kind="decision") == 0  # the limits themselves are fine
    assert _add("--why", "w" * 300) == 0


def test_add_refuses_when_the_new_line_would_be_hidden_and_writes_nothing(tmp_path, monkeypatch, capsys):
    f = _add_env(tmp_path, monkeypatch)
    f.parent.mkdir()
    f.write_text(decisions.render_file(1001) + "```\n" + OPEN + "\n", encoding="utf-8")  # unclosed fence
    before = f.read_bytes()
    assert _add() == 2
    assert "decisions file" in capsys.readouterr().err and f.read_bytes() == before and list(f.parent.glob("*.tmp")) == []
    f.write_bytes(b"\xff\xfe")
    assert _add() == 2 and f.read_bytes() == b"\xff\xfe"


def test_add_write_is_atomic(tmp_path, monkeypatch, capsys):
    f = _add_env(tmp_path, monkeypatch)
    assert _add() == 0
    before = f.read_bytes()
    f.chmod(0o640)
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert _add(text="more") == 2
    assert "disk full" in capsys.readouterr().err and f.read_bytes() == before and list(f.parent.glob("*.tmp")) == []
    monkeypatch.setattr(os, "replace", real)
    assert _add(text="more") == 0 and (f.stat().st_mode & 0o777) == 0o640
    f2 = f.with_name("Decisions_1002.md")
    assert main(["decisions", "1002", "--add", "t", "--kind", "decision"]) == 0 and f2.exists()


def test_add_flag_combinations_exit_2(tmp_path, monkeypatch):
    f = _add_env(tmp_path, monkeypatch)
    for argv in (["--add", "t"], ["--add", "t", "--kind", "decision", "--post"], ["--add", "t", "--kind", "decision", "--apply"],
                 ["--add", "t", "--kind", "decision", "--resolve", "D1", "--validated"],
                 ["--add", "t", "--kind", "decision", "--plan-out", "p.md"],
                 ["--add", "t", "--kind", "decision", "--validated"], ["--add", "t", "--kind", "decision", "--by", "PO"],
                 ["--kind", "decision"], ["--why", "w"]):
        with pytest.raises(SystemExit) as e:
            main(["decisions", "1001", *argv])
        assert e.value.code == 2, argv
    assert not f.exists()


def test_post_and_resolve_use_the_file(tmp_path, monkeypatch, capsys):
    m, fake, f = _env(tmp_path, monkeypatch, OPEN + "\n" + OPEN2)
    other = tmp_path / "dod" / "DoD_1001.md"
    other.write_text("# DoD\n" + OPEN.replace("D1", "D9") + "\n", encoding="utf-8")  # a D line in a ledger is never read
    ledger_before = other.read_bytes()
    assert m.main(["decisions", "1001", "--post", "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["lines"] == ["D1", "D4"]
    md = f.read_text(encoding="utf-8")
    assert f"posted: 777:{H1}" in md and f"posted: 777:{H2}" in md
    _tty(monkeypatch)
    assert _resolve(m, "D1", "--validated") == 0
    assert "- [x] D1:" in f.read_text(encoding="utf-8")
    assert _resolve(m, "D9", "--validated") == 2  # only in the ledger
    assert other.read_bytes() == ledger_before
    f.unlink()
    assert m.main(["decisions", "1001", "--post"]) == 2 and _resolve(m, "D1", "--validated") == 2
    assert "`ado decisions 1001 --add " in capsys.readouterr().err


def test_add_counts_ids_hidden_in_comments_and_fences(tmp_path, monkeypatch):
    f = _add_env(tmp_path, monkeypatch)
    f.parent.mkdir()
    f.write_text(decisions.render_file(1001) + f"<!-- {VAL.replace('D2', 'D12')} -->\n```\n{REJ.replace('D3', 'D20')}\n```\n" + OPEN + "\n",
                 encoding="utf-8")
    assert _add() == 0
    assert "- [ ] D21: Use cursor paging" in f.read_text(encoding="utf-8")


# ---- V3-15: `--if-any` makes a missing decisions file a no-op for --post (the workflow's decisions-loop) ----
def test_if_any_missing_file_is_a_noop_exit_0_and_writes_nothing(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    led.unlink()
    for argv in (["decisions", "1001", "--post", "--if-any"], ["decisions", "1001", "--if-any", "--plan-out", "p.md"]):
        assert m.main(argv) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["action"] == "none" and out["lines"] == [] and out["applied"] is False and "no decisions file" in out["note"]
    assert "nothing to post" in (tmp_path / "p.md").read_text(encoding="utf-8")
    assert m.main(["decisions", "1001", "--post", "--if-any", "--apply", "--expect-plan", "p.md"]) == 0
    assert json.loads(capsys.readouterr().out)["comment_id"] is None
    assert _writes(fake) == [] and not led.exists()  # the apply never creates the file either


def test_if_any_apply_refuses_when_the_file_appeared_after_the_empty_preview(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    led.unlink()
    assert m.main(["decisions", "1001", "--post", "--if-any", "--plan-out", "p.md"]) == 0
    led.write_text(_file(OPEN), encoding="utf-8")  # a decision was recorded between the preview and the apply
    capsys.readouterr()
    assert m.main(["decisions", "1001", "--post", "--if-any", "--apply", "--expect-plan", "p.md"]) == 2
    assert "plan changed since approval" in capsys.readouterr().err and "D1" in (tmp_path / "p.md.new").read_text()
    assert _writes(fake) == []


def test_if_any_with_a_file_behaves_like_plain_post_and_without_it_a_missing_file_still_exits_2(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, OPEN)
    assert m.main(["decisions", "1001", "--post", "--if-any"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "add" and out["lines"] == ["D1"] and "note" not in out
    led.unlink()
    assert m.main(["decisions", "1001", "--post"]) == 2
    assert "no decisions file" in capsys.readouterr().err


def test_if_any_flag_combinations_exit_2(capsys):
    for argv in (["decisions", "1001", "--if-any"], ["decisions", "1001", "--if-any", "--add", "x", "--kind", "decision"],
                 ["decisions", "1001", "--if-any", "--resolve", "D1", "--validated"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2


# ---- V3-16a: `supersedes D<n>: ` answers a rejected (or open) decision ----
def _dl(n, text, status="open", kind="decision", posted=False):
    """One decisions-file line (exact grammar) for `D<n>`; `posted`: with the marker the PO's thread would give it."""
    d = {"id": f"D{n}", "kind": kind, "text": text, "why": "w", "status": status, "by": "" if status == "open" else "PO",
         "note": "", "posted": None, "used": ""}
    if posted:
        d["posted"] = {"comment_id": 7, "hash": decisions.marker_hash(_first(d))}
    return decisions.render_line(d) + "\n"


def _first(d):
    """The entry `parse_decisions` reads back from `d` (so a supersedes prefix is seen)."""
    p = decisions.parse_decisions(decisions.render_line(d) + "\n")
    return (p["open"] + p["validated"] + p["rejected"])[0]


def _parse(*lines):
    return decisions.parse_decisions(decisions.render_file(1001) + "".join(lines))


def _ids(parsed):
    return [d["id"] for k in ("open", "validated", "rejected") for d in parsed[k]]


def test_parse_extracts_supersedes_only_from_the_exact_prefix():
    r = _parse(_dl(3, "x", "rejected"), _dl(5, "supersedes D3: use v2"), _dl(6, "supersedes D999999: a"))
    [d5, d6] = r["open"]
    assert d5["supersedes"] == "D3" and d5["text"] == "supersedes D3: use v2" and d6["supersedes"] == "D999999"
    assert "supersedes" not in r["rejected"][0]  # no key at all on an ordinary line: v0.2 listings stay byte-identical
    for text in ("Supersedes D3: a", "supersedes d3: a", "supersedes  D3: a", "supersedes D3 : a", "supersedes D3:a", "supersedes D3 a",
                 "please supersedes D3: a", "supersedes D1234567: a", "supersedes D: a", "supersedes D３: a", "supersedes D٣: a",
                 "supersedes D3:"):
        assert _parse(_dl(5, text))["open"][0].get("supersedes") is None, text
    line = _dl(5, "supersedes D3: use v2").rstrip()
    assert decisions.render_line(_parse(line)["open"][0]) == line  # the grammar is unchanged: it round-trips


def test_effective_drops_the_superseded_entry_unless_the_superseder_is_rejected():
    base = (_dl(3, "x", "rejected"),)
    assert _ids(decisions.effective(_parse(*base, _dl(5, "supersedes D3: y")))) == ["D5"]  # open is live
    assert _ids(decisions.effective(_parse(*base, _dl(5, "supersedes D3: y", "validated")))) == ["D5"]
    assert _ids(decisions.effective(_parse(*base, _dl(5, "supersedes D3: y", "rejected")))) == ["D3", "D5"]  # re-activated
    plain = _parse(_dl(1, "a"), _dl(2, "b", "validated"), _dl(3, "c", "rejected"))
    assert decisions.effective(plain) == plain  # nothing superseded: the same entries
    assert decisions.effective(_parse(*base))["unparsed"] is False
    assert decisions.effective({**_parse(*base), "unparsed": True})["unparsed"] is True


def test_chain_and_cycle_and_self_reference_and_unknown_target():
    d3 = _dl(3, "x", "rejected")
    # chain: D7 answers D5 which answers D3; a live head clears the whole chain, a rejected head re-opens the one below it
    chain = lambda s5, s7: _parse(d3, _dl(5, "supersedes D3: a", s5), _dl(7, "supersedes D5: b", s7))
    assert _ids(decisions.effective(chain("open", "open"))) == ["D7"]
    assert _ids(decisions.effective(chain("rejected", "open"))) == ["D7"]  # D7 stands in for the rejected D5, which stood in for D3
    assert _ids(decisions.effective(chain("rejected", "rejected"))) == ["D3", "D5", "D7"]
    assert _ids(decisions.effective(chain("open", "rejected"))) == ["D5", "D7"]  # D7 rejected: D5 is back, and D5 still answers D3
    assert decisions.supersede_problem(_parse(_dl(1, "supersedes D2: a"), _dl(2, "supersedes D1: b"))) is True  # cycle
    for lines in ((_dl(2, "supersedes D2: a"),),  # self-reference
                  (_dl(1, "supersedes D2: a"), _dl(2, "b", "rejected")),  # a lower id supersedes a higher one
                  (_dl(2, "supersedes D9: a"),),  # unknown target
                  (_dl(2, "supersedes D0: a"),)):
        p = _parse(*lines)
        assert decisions.supersede_problem(p) is True and decisions.effective(p) == p, lines  # invalid edges drop nothing
    assert decisions.supersede_problem(_parse(d3, _dl(5, "supersedes D3: a"))) is False
    assert decisions.supersede_problem(_parse(_dl(1, "a"))) is False


def test_override_cannot_be_superseded():
    p = _parse(_dl(1, "skip l2", kind="override"), _dl(2, "supersedes D1: a"))
    assert decisions.supersede_problem(p) is True
    assert _ids(decisions.effective(p)) == ["D1", "D2"]  # the override stays in the set


def test_unposted_keeps_an_answered_open_decision_the_po_never_saw():
    p = _parse(_dl(3, "old", "rejected"), _dl(4, "old open"), _dl(5, "supersedes D4: new"))
    assert [d["id"] for d in decisions.unposted(p)] == ["D4", "D5"]  # answering D4 must not hide it from the PO
    assert decisions.plan_post(None, 1001, p)["lines"] == ["D4", "D5"]
    p = _parse(_dl(4, "old open", posted=True), _dl(5, "supersedes D4: new"))
    assert [d["id"] for d in decisions.unposted(p)] == ["D5"]
    p = _parse(_dl(4, "old open"), _dl(5, "supersedes D4: new", "rejected"))
    assert [d["id"] for d in decisions.unposted(p)] == ["D4"]
    assert "supersedes D4: new" in decisions.plan_post(None, 1001, _parse(_dl(4, "a", "rejected"), _dl(5, "supersedes D4: new")))["html"]


def _addmd(md, text="my reply", supersedes=None, kind="decision"):
    return decisions.add(md, kind, text, "w", supersedes=supersedes)


def test_add_supersedes_flag_validates_target_exists_and_is_rejected_or_open():
    md = (decisions.render_file(1001) + _dl(1, "a", "rejected") + _dl(2, "b", posted=True) + _dl(3, "c", "validated")
          + _dl(4, "skip l2", kind="override"))
    new, d_id = _addmd(md, supersedes="D1")
    assert d_id == "D5" and new.endswith("- [ ] D5: supersedes D1: my reply → _(open)_ · kind: decision · why: w\n")
    assert decisions.parse_decisions(new)["open"][-1]["supersedes"] == "D1" and not decisions.supersede_problem(decisions.parse_decisions(new))
    assert _addmd(md, supersedes="D2")[1] == "D5"  # an open target too
    for bad, msg in (("D9", "not in the decisions file"), ("D3", "rejected or open"), ("D4", "override"), ("3", "D<n>"), ("D", "D<n>"),
                     ("d1", "D<n>"), ("D1234567", "D<n>"), ("D１", "D<n>"), ("D1 ", "D<n>"), ("", "D<n>")):
        with pytest.raises(ValueError) as e:
            _addmd(md, supersedes=bad)
        assert msg in str(e.value) and "my reply" not in str(e.value), bad
    # a second LIVE superseder of the same target is refused, naming the existing one
    md2, _ = _addmd(md, supersedes="D1")
    with pytest.raises(ValueError, match="D5"):
        _addmd(md2, supersedes="D1")
    with pytest.raises(ValueError, match="post D5 first"):  # D5 is open and the PO has not seen it
        _addmd(md2, supersedes="D5")
    md2p = decisions._marked(md2, decisions.plan_post(None, 1001, decisions.parse_decisions(md2))["hashes"], 7)
    assert _addmd(md2p, supersedes="D5")[1] == "D6"  # a chain is fine once D5 is posted
    # once that superseder is rejected it is not live: the target may be answered again
    md3 = md2.replace("- [ ] D5: supersedes D1: my reply → _(open)_ · kind: decision · why: w",
                      "- [x] D5: supersedes D1: my reply → rejected · by: PO · note: no")
    assert decisions.parse_decisions(md3)["rejected"][-1]["id"] == "D5"
    assert _addmd(md3, supersedes="D1")[1] == "D6"
    # the prefix cannot be typed by hand, with or without the flag
    for sup in (None, "D1"):
        with pytest.raises(ValueError, match="supersedes D<n>"):
            _addmd(md, text="supersedes D1: sneaky", supersedes=sup)
    assert decisions.add(md, "decision", "mentions supersedes D1: later", "w")[1] == "D5"  # only a leading prefix counts


def test_add_supersedes_cli(tmp_path, monkeypatch, capsys):
    f = _add_env(tmp_path, monkeypatch)
    f.parent.mkdir()
    f.write_text(decisions.render_file(1001) + _dl(1, "use v1", "rejected"))
    assert _add("--supersedes", "D1", text="use v2 as the PO asked") == 0
    assert json.loads(capsys.readouterr().out)["id"] == "D2"
    assert f.read_text().endswith("- [ ] D2: supersedes D1: use v2 as the PO asked → _(open)_ · kind: decision · why: unspecified\n")
    before = f.read_bytes()
    assert _add("--supersedes", "D1") == 2 and _add("--supersedes", "D7") == 2 and _add("--supersedes", "x") == 2
    assert f.read_bytes() == before
    err = capsys.readouterr().err
    assert "D2" in err and "not in the decisions file" in err
    with pytest.raises(SystemExit) as e:
        main(["decisions", "1001", "--supersedes", "D1"])
    assert e.value.code == 2 and f.read_bytes() == before


def test_a_superseder_marker_covers_its_id_so_a_copy_on_another_id_does_not_match():
    d9 = _first({"id": "D9", "kind": "decision", "text": "supersedes D3: x", "why": "w", "status": "open", "by": "", "note": "",
                 "posted": None, "used": ""})
    d10 = {**d9, "id": "D10"}
    assert decisions.marker_hash(d9) != decisions.marker_hash(d10)
    plain = {**d9, "text": "x", "supersedes": None}
    plain.pop("supersedes")
    assert decisions.marker_hash(plain) == decisions.line_hash("x")  # an ordinary line: text only, as before


def test_supersede_unposted_lists_every_superseder_and_every_open_target_without_a_matching_marker():
    f = decisions.supersede_unposted
    base = (_dl(3, "x", "rejected"),)
    for status, kind in (("open", "decision"), ("validated", "decision"), ("rejected", "decision"), ("open", "assumption"),
                         ("validated", "assumption")):
        assert [d["id"] for d in f(_parse(*base, _dl(9, "supersedes D3: y", status, kind)))] == ["D9"], (status, kind)
        assert f(_parse(*base, _dl(9, "supersedes D3: y", status, kind, posted=True))) == [], (status, kind)
    # a copy of a posted line's text and marker on another id does not match
    posted = _dl(9, "supersedes D3: y", "rejected", posted=True)
    assert [d["id"] for d in f(_parse(*base, posted, posted.replace("D9", "D10")))] == ["D10"]
    # an open target the effective set drops needs its own marker; an ordinary unposted open line is nobody's business here
    assert [d["id"] for d in f(_parse(_dl(4, "old"), _dl(5, "supersedes D4: n", posted=True)))] == ["D4"]
    assert f(_parse(_dl(4, "old", posted=True), _dl(5, "supersedes D4: n", posted=True))) == []
    assert f(_parse(_dl(4, "old"), _dl(5, "supersedes D4: n", "rejected", posted=True))) == []  # not dropped: re-activated
    assert f(_parse(_dl(1, "plain"), _dl(2, "plain too", "rejected"))) == []
    chain = _parse(base[0], _dl(5, "supersedes D3: a", "rejected"), _dl(7, "supersedes D5: b", posted=True))
    assert [d["id"] for d in f(chain)] == ["D5"]  # a chain linked by a line that was never posted


def test_masked_by_names_the_standing_superseder():
    p = _parse(_dl(3, "x", "rejected"), _dl(5, "supersedes D3: a"), _dl(6, "supersedes D3: b", "rejected"))
    assert decisions.masked_by(p, "D3") == "D5" and decisions.masked_by(p, "D5") is None and decisions.masked_by(p, "D6") is None
    assert decisions.masked_by(_parse(_dl(3, "x", "rejected"), _dl(5, "supersedes D3: a", "rejected")), "D3") is None


def test_resolve_on_a_superseded_decision_prints_a_notice_and_changes_nothing_else(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, _dl(3, "x").rstrip() + "\n" + _dl(5, "supersedes D3: a").rstrip() + "\n" + _dl(6, "plain").rstrip())
    _tty(monkeypatch)
    assert _resolve(m, "D3", "--rejected") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["notice"] == "D3 is superseded by D5: this verdict is masked while D5 stands (a rejected D5 would bring it back)"
    d = decisions.parse_decisions(led.read_text(encoding="utf-8"))
    assert [x["id"] for x in d["rejected"]] == ["D3"]  # the verdict is recorded as always
    assert _resolve(m, "D6", "--validated") == 0
    assert "notice" not in json.loads(capsys.readouterr().out)


# ---- P4-9: `ado decisions <id> --sync` (an approver's /approve D<n> or /reject D<n> comment becomes the verdict, locally) ----
import copy  # noqa: E402

from ado import __main__ as _main  # noqa: E402
from ado import approvals, comments  # noqa: E402
from ado.client import AdoError  # noqa: E402
from ado.config import Trust  # noqa: E402
from tests.conftest import comment_pages, connection_data, raw_comment, versions_response  # noqa: E402

TOK, KITID, APPR = "aaaa1111-0000-4000-8000-00000000000a", "4444444a-4444-4444-8444-44444444444b", "55555555-5555-4555-8555-555555555555"
T_POST, T_V1, T_V2, T_V3 = "2026-10-01T10:00:00Z", "2026-10-01T11:00:00Z", "2026-10-01T12:00:00Z", "2026-10-01T13:00:00Z"
S1 = "- [ ] D1: Use cursor paging → _(open)_ · kind: decision · why: the story names no page size"
S2 = "- [ ] D2: Cap at 50 rows → _(open)_ · kind: assumption · why: the story is silent"
S3 = "- [ ] D3: Skip audit log → _(open)_ · kind: decision · why: out of scope"


def _posted(*lines, cid=10):
    """(the decisions file with every line marked as posted in comment `cid`, the exact comment html the kit would have posted)."""
    md = _file("\n".join(lines))
    plan = decisions.plan_post(None, 1001, decisions.parse_decisions(md))
    return decisions._marked(md, plan["hashes"], cid), plan["html"]


def _rows(cfg, raws):
    found, truncated = comments.fetch(FakeClient(cfg, responses=comment_pages(raws)), 1001, with_meta=True)
    assert not truncated
    return found


def _kit(html, cid=10, date=T_POST, author=KITID, **kw):
    return raw_comment(cid, html, date, author=author, **kw)


def _vote(cid, text, date=T_V1, author=APPR, **kw):
    return raw_comment(cid, f"<p>{text}</p>", date, author=author, **kw)


def _sync(cfg, md, raws, approvers=(APPR,), excluded=(TOK, KITID), responses=()):
    client = FakeClient(cfg, responses=list(responses))
    return decisions.sync(md, _rows(cfg, raws), list(approvers), set(excluded), client, 1001), client


def test_sync_validates_and_rejects_with_via(cfg):
    md, html = _posted(S1, S2, S3)
    (new, res), client = _sync(cfg, md, [_kit(html), _vote(11, "/approve D1"), _vote(12, "/reject D2: not now &lt;!-- x --&gt;", T_V2)])
    assert res["applied"] == ["D1", "D2"] and res["skipped"] == {"D3": "no-verdict"} and res["bad_newer"]["D1"] == 0
    got = decisions.parse_decisions(new)
    assert got["unparsed"] is False
    d1, d2, d3 = got["validated"][0], got["rejected"][0], got["open"][0]
    assert (d1["id"], d1["by"], d1["via"]) == ("D1", "approver", "11") and d1["posted"]["comment_id"] == 10
    assert (d2["id"], d2["by"], d2["via"]) == ("D2", "approver", "12") and "<!--" not in d2["note"] and "-->" not in d2["note"]
    assert d3["id"] == "D3" and "via" not in d3  # an entry with no via has no `via` key at all
    assert all(c["method"] == "GET" for c in client.calls)  # no ADO write, ever
    # the terminal's resolve machinery wrote them: the same line a human would get, plus `via`
    assert [ln for ln in new.splitlines() if ln.startswith("- [x] D1")][0].count(" · via: 11") == 1
    assert decisions.render_line(d1) == [ln for ln in new.splitlines() if ln.startswith("- [x] D1")][0]


def test_sync_skips_unposted_or_edited_line(cfg):
    old, html = _posted(S2.replace("D2", "D5").replace("Cap at 50 rows", "Retry twice"), S2, cid=10)
    d2_edited = [ln for ln in old.splitlines() if ln.startswith("- [ ] D2")][0].replace("Cap at 50 rows", "Cap at 5000 rows")
    h = decisions.line_hash("Retry thrice")
    lines = [
        S1,                                                                                         # never posted
        d2_edited,                                                                                  # edited after posting: marker hash is stale
        S3 + f" · posted: 99:{decisions.line_hash('Skip audit log')}",                              # the marker names no comment
        "- [ ] D4: Old news → _(open)_ · kind: decision · why: w" + f" · posted: 20:{decisions.line_hash('Old news')}",  # an unrelated comment
        f"- [ ] D5: Retry thrice → _(open)_ · kind: assumption · why: the story is silent · posted: 10:{h}",  # re-hashed after the edit
    ]
    raws = [_kit(html), raw_comment(20, "<p>an old note</p>", "2026-09-01T00:00:00Z", author=APPR)]
    raws += [_vote(30 + n, f"/approve D{n}") for n in range(1, 6)]
    (new, res), _ = _sync(cfg, _file("\n".join(lines)), raws)
    assert res["applied"] == [] and new == _file("\n".join(lines))
    assert res["skipped"] == {"D1": "not-posted", "D2": "edited", "D3": "posting-missing", "D4": "posting-mismatch", "D5": "posting-mismatch"}


def test_sync_posting_comment_must_be_the_kits_and_hold_the_line(cfg):
    md, html = _posted(S1)
    for raws, rule in (([_kit(html, author=APPR)], "posting-mismatch"),      # the posting is not by an excluded (kit) identity
                       ([_kit(html.replace("Use cursor paging", "Use offset paging"))], "posting-mismatch"),
                       ([_kit("<p>looks fine</p>")], "posting-mismatch"),
                       ([_kit(html, date="not a date")], "posting-missing")):
        (new, res), _ = _sync(cfg, md, raws + [_vote(11, "/approve D1", T_V2)])
        assert res["applied"] == [] and new == md and res["skipped"] == {"D1": rule}, raws


def test_sync_skips_verdict_older_than_posting(cfg):
    md, html = _posted(S1, S2, S3)
    raws = [_kit(html, cid=10), _vote(8, "/approve D1", "2026-10-01T09:00:00Z"), _vote(9, "/approve D2", T_POST), _vote(11, "/approve D3", T_POST)]
    (new, res), _ = _sync(cfg, md, raws)
    assert res["applied"] == ["D3"] and res["skipped"] == {"D1": "older-than-posting", "D2": "older-than-posting"}
    # no local clock: a verdict is newer by the comment's created time, then its id
    assert [d["id"] for d in decisions.parse_decisions(new)["validated"]] == ["D3"]


def test_sync_ignores_commands_that_do_not_count(cfg):
    md, html = _posted(S1, S2, S3)
    raws = [_kit(html), _vote(11, "/approve D1", author=TOK), _vote(12, "/approve D2", author="66666666-6666-4666-8666-666666666666"),
            _vote(13, "<blockquote>/approve D3</blockquote>"), _vote(14, "thanks!<br>/approve D3"), _vote(15, "/approve D3", on_behalf=APPR),
            _vote(16, "/approve D1", author=KITID)]
    (new, res), _ = _sync(cfg, md, raws)
    assert res["applied"] == [] and new == md and set(res["skipped"].values()) == {"no-verdict"}


def test_newer_reject_overrides_synced_approve(cfg):
    md, html = _posted(S1, S2)
    base = [_kit(html), _vote(11, "/approve D1"), _vote(12, "/approve D2")]
    (md2, res), _ = _sync(cfg, md, base)
    assert res["applied"] == ["D1", "D2"]
    (same, res), _ = _sync(cfg, md2, base)  # idempotent: the second run changes nothing
    assert same == md2 and res["applied"] == [] and res["skipped"] == {"D1": "up-to-date", "D2": "up-to-date"}
    later = base + [_vote(13, "/reject D1: changed my mind", T_V3), _vote(14, "/reject D2", T_V3, author=TOK)]  # D2: the agent's reject does not count
    (md3, res), _ = _sync(cfg, md2, later)
    assert res["applied"] == ["D1"] and res["skipped"] == {"D2": "up-to-date"}
    got = decisions.parse_decisions(md3)
    assert [(d["id"], d["via"], d["note"]) for d in got["rejected"]] == [("D1", "13", "changed my mind")]
    assert [(d["id"], d["via"]) for d in got["validated"]] == [("D2", "12")] and got["unparsed"] is False
    # and the other way round: the approver's own later /approve wins again
    (md4, res), _ = _sync(cfg, md3, later + [_vote(15, "/approve D1", "2026-10-01T14:00:00Z")])
    assert res["applied"] == ["D1"]
    assert {d["id"]: d["via"] for d in decisions.parse_decisions(md4)["validated"]} == {"D1": "15", "D2": "12"}


def test_an_edited_and_reposted_line_does_not_take_the_old_verdict(cfg):
    md, html = _posted(S1, cid=10)
    synced = _sync(cfg, md, [_kit(html), _vote(11, "/approve D1")])[0][0]
    # the agent rewords D1, the kit posts it again (comment 20) and the marker moves: the old approval is about other words
    edited = S1.replace("cursor paging", "offset paging")
    md2, html2 = _posted(edited, cid=20)
    (new, res), _ = _sync(cfg, md2, [_kit(html), _kit(html2, cid=20, date=T_V3), _vote(11, "/approve D1")])
    assert new == md2 and res["skipped"] == {"D1": "older-than-posting"}
    (new, res), _ = _sync(cfg, synced.replace("cursor paging", "offset paging").replace("posted: 10:" + decisions.line_hash("Use cursor paging"),
                                                                                       "posted: 20:" + decisions.line_hash("Use offset paging")),
                          [_kit(html), _kit(html2, cid=20, date=T_V3), _vote(11, "/approve D1")])
    assert res["applied"] == [] and res["skipped"] == {"D1": "older-than-posting"}  # the stale verdict is not renewed (the done-time cross-check flags it)


def test_an_edited_or_foreign_later_reject_does_not_override_a_synced_approve(cfg):
    md, html = _posted(S1)
    synced = _sync(cfg, md, [_kit(html), _vote(11, "/approve D1")])[0][0]
    base = [_kit(html), _vote(11, "/approve D1")]
    edited = base + [_vote(12, "/reject D1", T_V3, version=2)]
    (new, res), _ = _sync(cfg, synced, edited, responses=[versions_response((1, "<p>/approve D1</p>", T_V3), (2, "<p>/reject D1</p>", T_V3))])
    assert new == synced and res["skipped"] == {"D1": "up-to-date"}
    foreign = base + [_vote(12, "/reject D1", T_V3, author="66666666-6666-4666-8666-666666666666"), _vote(13, "/reject D1", T_V3, author=TOK)]
    (new, res), _ = _sync(cfg, synced, foreign)
    assert new == synced and res["skipped"] == {"D1": "up-to-date"}


def test_sync_never_overwrites_a_verdict_written_in_a_terminal(cfg):
    md, html = _posted(S1)
    md = decisions.resolve(md, "D1", "validated", "PO", "agreed", False)  # no via
    (new, res), _ = _sync(cfg, md, [_kit(html), _vote(11, "/reject D1", T_V2)])
    assert new == md and res["skipped"] == {"D1": "resolved-locally"}


def test_sync_refuses_a_malformed_or_ambiguous_file(cfg):
    md, html = _posted(S1)
    raws = [_kit(html), _vote(11, "/approve D1")]
    for bad in (md + "- [ ] D2: broken line\n", md + "<!-- never closed\n", md + S1 + "\n", md + "- [ ] D1: copy → _(open)_ · kind: decision · why: w\n"):
        with pytest.raises(ValueError, match="malformed"):
            _sync(cfg, bad, raws)


def test_a_hand_written_via_is_replaced_by_the_verified_comment(cfg):
    md, html = _posted(S1, S2, S3)
    fake = decisions.resolve(md, "D1", "validated", "approver", "", False, "777")
    fake = decisions.resolve(fake, "D2", "validated", "approver", "", False, "778")
    (new, res), _ = _sync(cfg, fake, [_kit(html), _vote(11, "/approve D1"), _vote(12, "/reject D2")])
    got = decisions.parse_decisions(new)
    assert res["applied"] == ["D1", "D2"] and [(d["id"], d["via"]) for d in got["validated"]] == [("D1", "11")]
    assert [(d["id"], d["via"]) for d in got["rejected"]] == [("D2", "12")]
    # no approver comment at all: the hand-written verdict is left as it is (the done-time cross-check is what flags it)
    (new, res), _ = _sync(cfg, fake, [_kit(html)])
    assert new == fake and res["skipped"]["D1"] == "no-verdict"


def test_sync_ids_are_ascii_digits_only(cfg):
    md, html = _posted(S1)
    for bad in ("- [ ] D\u0663: x → _(open)_ · kind: decision · why: w", "- [ ] D" + "9" * 12 + ": x → _(open)_ · kind: decision · why: w"):
        with pytest.raises(ValueError, match="malformed"):
            _sync(cfg, md + bad + "\n", [_kit(html)])


def test_bad_newer_is_reported_next_to_the_verdict(cfg):
    md, html = _posted(S1, S2)
    raws = [_kit(html), _vote(11, "/approve D1"), _vote(12, "/reject D1 nope", T_V2), _vote(13, "/approve D2")]
    (new, res), _ = _sync(cfg, md, raws)
    assert res["applied"] == ["D1", "D2"] and res["bad_newer"] == {"D1": 1, "D2": 0}  # a retraction typed wrong cannot be told from an approval; one that names D1 is not about D2
    assert all(isinstance(v, int) for v in res["bad_newer"].values())


def test_sync_reads_the_edited_verdict_via_the_guards(cfg):
    md, html = _posted(S1)
    edited = [_kit(html), _vote(11, "/approve D1", version=2)]
    (new, res), _ = _sync(cfg, md, edited, responses=[versions_response((1, "<p>/reject D1</p>", T_V1), (2, "<p>/approve D1</p>", T_V1))])
    assert new == md and res["skipped"] == {"D1": "no-verdict"}


def test_sync_changes_only_target_lines_crlf_kept(tmp_path, cfg):
    md, html = _posted(S1, S2, S3)
    crlf = (md + "A trailing prose line\n").replace("\n", "\r\n") + "last line, no terminator"
    path = tmp_path / "Decisions_1001.md"
    path.write_bytes(crlf.encode("utf-8"))
    res = decisions.apply_sync(path, _rows(cfg, [_kit(html), _vote(11, "/reject D2: no")]), [APPR], {TOK, KITID}, FakeClient(cfg), 1001)
    assert res["applied"] == ["D2"]
    old, new = crlf.encode().split(b"\r\n"), path.read_bytes().split(b"\r\n")
    assert len(old) == len(new) and [i for i, (a, b) in enumerate(zip(old, new)) if a != b] == [next(i for i, ln in enumerate(old) if ln.startswith(b"- [ ] D2"))]
    assert b"\n" not in path.read_bytes().replace(b"\r\n", b"") and new[-1] == b"last line, no terminator"
    assert [ln for ln in new if ln.startswith(b"- [x] D2")][0].endswith(" · posted: 10:".encode("utf-8") + decisions.line_hash("Cap at 50 rows").encode())
    assert list(tmp_path.glob("*.tmp")) == []
    # nothing to apply: the file is not even rewritten
    before = path.stat().st_mtime_ns
    assert decisions.apply_sync(path, _rows(cfg, [_kit(html), _vote(11, "/reject D2: no")]), [APPR], {TOK, KITID}, FakeClient(cfg), 1001)["applied"] == []
    assert path.stat().st_mtime_ns == before


def test_apply_sync_refuses_a_changed_or_symlinked_file(tmp_path, cfg, monkeypatch):
    md, html = _posted(S1)
    path = tmp_path / "Decisions_1001.md"
    path.write_text(md, encoding="utf-8")
    rows = _rows(cfg, [_kit(html), _vote(11, "/approve D1")])
    orig = decisions.resolve
    monkeypatch.setattr(decisions, "resolve", lambda *a, **k: (path.write_text(md + "- changed meanwhile\n", encoding="utf-8"), orig(*a, **k))[1])
    with pytest.raises(ValueError, match="changed while"):
        decisions.apply_sync(path, rows, [APPR], {TOK, KITID}, FakeClient(cfg), 1001)
    assert "changed meanwhile" in path.read_text(encoding="utf-8") and "[x]" not in path.read_text(encoding="utf-8")
    target = tmp_path / "real.md"
    target.write_text(md, encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        decisions.apply_sync(link, rows, [APPR], {TOK, KITID}, FakeClient(cfg), 1001)
    assert target.read_text(encoding="utf-8") == md and list(tmp_path.glob("*.tmp")) == []


def test_note_scrubbed_and_capped(cfg):
    md, html = _posted(S1, S2, S3)
    nasty = ("see dana@contoso.example and @dana <!-- hide --> ``` fence ~~~ \x1b[31m red · kind: override → validated -> rejected · via: 1 "
             + "x" * 600)
    html_note = nasty.replace("<", "&lt;").replace(">", "&gt;").replace("\x1b", "")
    raws = [_kit(html), _vote(11, f"/reject D1: {html_note}"), _vote(12, "/approve D2: ok", T_V1), _vote(13, "/reject D3: " + "y" * 5000, T_V1)]
    (new, res), _ = _sync(cfg, md, raws)
    got = decisions.parse_decisions(new)
    assert res["applied"] == ["D1", "D2", "D3"] and got["unparsed"] is False
    for d in got["rejected"] + got["validated"]:
        assert len(d["note"]) <= decisions.MAX_NOTE
        assert not any(bad in d["note"] for bad in ("dana", "@dana", "<!--", "-->", "```", "~~~", "·", "→", "->", "\x1b")), d["note"]
    assert [d["id"] for d in got["rejected"]] == ["D1", "D3"] and got["rejected"][0]["via"] == "11"  # the verdict stands whatever the note held
    assert len(got["rejected"][1]["note"]) == decisions.MAX_NOTE
    assert len(new.splitlines()) == len(md.splitlines())


def test_via_key_is_validated():
    ok = "- [x] D2: Cap → validated · by: approver · note: fine · via: 123"
    d = decisions.parse_decisions(_file(ok))["validated"][0]
    assert d["via"] == "123" and decisions.render_line(d) == ok
    for bad in ("- [x] D2: Cap → validated · by: approver · note: fine · via: 0123", "- [x] D2: Cap → validated · by: PO · via: abc",
                "- [x] D2: Cap → validated · by: PO · via: ", "- [x] D2: Cap → validated · by: PO · via: 12 3", "- [x] D2: Cap → validated · by: PO · via: 1234567890123",
                "- [x] D2: Cap → validated · by: PO · via: 1 · via: 2", "- [x] D2: Cap → validated · by: PO · via: ١٢٣",
                "- [ ] D2: Cap → _(open)_ · kind: decision · why: w · via: 12"):
        r = decisions.parse_decisions(_file(bad))
        assert r["unparsed"] is True and not (r["open"] or r["validated"] or r["rejected"]), bad
    with pytest.raises(ValueError):
        decisions.resolve(_file(S1), "D1", "validated", "approver", "", False, "12; rm")
    # a terminal verdict over a synced one drops the via key: it no longer claims a comment
    synced = decisions.resolve(_file(S1), "D1", "validated", "approver", "", False, "12")
    assert " · via: 12" in synced and " · via:" not in decisions.resolve(synced, "D1", "rejected", "PO", "no", True)


def test_via_key_keeps_existing_fixture_splits(monkeypatch):
    """Every decision line that the existing tests write parses the same before and after `_KEYS` gained `via`."""
    root = Path(__file__).parent
    found = set()
    for f in sorted(root.glob("test_*.py")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"- \[[ xX]\] D\d+[^\"'\n]*", text.split("# ---- P4-9")[0]):  # the P4-9 tests themselves are about `via`
            found.add(m.group())
    found |= {OPEN, VAL, REJ, KEPT, OPEN2, S1, S2, S3, "- [x] D2: Cap → validated · by: PO · note: see via: 12 and so on", "- [x] D2: Cap → validated · by: PO · note: a via: b"}
    assert len(found) > 40
    new = {ln: decisions.parse_decisions(_file(ln)) for ln in found}
    keys = tuple(k for k in decisions._KEYS if k != "via")
    assert keys != decisions._KEYS
    monkeypatch.setattr(decisions, "_KEYS", keys)
    monkeypatch.setattr(decisions, "_SEP", re.compile(r"\s*·\s*(?=(?:" + "|".join(keys) + r")\s*:)"))
    for ln in sorted(found):
        assert decisions.parse_decisions(_file(ln)) == new[ln], ln


# ---- the verb ----
def _sync_env(tmp_path, monkeypatch, md, raws, approvers=(APPR,), bypass=False, extra=(), comment_responses=None, me=None):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    (tmp_path / "dod").mkdir()
    led = tmp_path / "dod" / "Decisions_1001.md"
    led.write_bytes(md.encode("utf-8"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust(list(approvers), "comment", {}))
    proj = {"id": "99999999-9999-4999-8999-999999999999", "name": "Fabrikam"}
    resp = [proj, {"count": 1, "value": [bypass]}] + (comment_pages(raws) if comment_responses is None else comment_responses) + [me or connection_data(TOK)] + list(extra)
    seen = {}

    def make(cfg):
        seen["c"] = FakeClient(cfg, responses=copy.deepcopy(resp))
        return seen["c"]
    monkeypatch.setattr(_main, "AdoClient", make)
    return led, seen


def test_sync_verb_prints_applied_skipped_and_bad_newer(tmp_path, monkeypatch, capsys):
    md, html = _posted(S1, S2, S3)
    led, seen = _sync_env(tmp_path, monkeypatch, md, [_kit(html), _vote(11, "/approve D1"), _vote(12, "/reject D1 not ok", T_V2), _vote(13, "/reject D2: no")])
    assert _main.main(["decisions", "1001", "--sync"]) == 0
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["applied"] == ["D1", "D2"] and out["skipped"] == {"D3": "no-verdict"} and out["bad_newer"] == {"D1": 1, "D2": 0, "D3": 0}
    assert out["status"] == {"D1": "validated", "D2": "rejected"} and any("D1" in n and "1 later malformed" in n for n in out["notice"])
    assert not any(c["method"] != "GET" for c in seen["c"].calls) and cap.err == ""
    got = decisions.parse_decisions(led.read_text(encoding="utf-8"))
    assert [d["id"] for d in got["validated"]] == ["D1"] and [d["id"] for d in got["rejected"]] == ["D2"]


def test_sync_refused_when_bypass_bit(tmp_path, monkeypatch, capsys):
    md, html = _posted(S1)
    for n, bypass in enumerate((True, None)):
        (tmp_path / str(n)).mkdir()
        led, seen = _sync_env(tmp_path / str(n), monkeypatch, md, [_kit(html), _vote(11, "/approve D1")], bypass=bypass)
        assert _main.main(["decisions", "1001", "--sync"]) == 2
        assert approvals.BYPASS_REFUSAL in capsys.readouterr().err and led.read_bytes() == md.encode("utf-8")
        assert not any("/comments" in c["path"] for c in seen["c"].calls)  # refused before the comments are read


def _fresh(tmp_path, name, monkeypatch, md, raws, **kw):
    (tmp_path / name).mkdir()
    return _sync_env(tmp_path / name, monkeypatch, md, raws, **kw)[0]


def test_sync_refusals_exit_2_write_nothing_and_echo_no_ado_text(tmp_path, monkeypatch, capsys):
    md, html = _posted(S1)
    good = [_kit(html), _vote(11, "/approve D1")]
    tok = {"comments": [raw_comment(1, "x", T_POST)], "continuationToken": "same"}
    cases = {"no_approvers": dict(approvers=()), "truncated": dict(comment_responses=[tok, tok]),
             "unreadable_page": dict(comment_responses=[{"comments": "SEKRET dana@contoso.example"}]),
             "unreadable_shape": dict(comment_responses=[{"comments": [{"id": "SEKRET"}]}]),
             "unknown_token_identity": dict(me=AdoError("HTTP 401: SEKRET dana@contoso.example", status=401))}
    for name, kw in cases.items():
        led = _fresh(tmp_path, name, monkeypatch, md, good, **kw)
        assert _main.main(["decisions", "1001", "--sync"]) == 2, name
        err = capsys.readouterr().err
        assert err.startswith("error:") and "SEKRET" not in err and "dana@" not in err and led.read_bytes() == md.encode("utf-8"), name
    for name, body in (("bad_file", md + "- [ ] D2: broken\n"), ("dup_file", md + S1 + "\n")):
        led = _fresh(tmp_path, name, monkeypatch, body, good)
        assert _main.main(["decisions", "1001", "--sync"]) == 2
        assert "malformed" in capsys.readouterr().err and led.read_bytes() == body.encode("utf-8")
    led = _fresh(tmp_path, "missing", monkeypatch, md, good)
    led.unlink()
    assert _main.main(["decisions", "1001", "--sync"]) == 2 and "decisions file" in capsys.readouterr().err
    led = _fresh(tmp_path, "linked", monkeypatch, md, good)
    real = led.with_name("real.md")
    real.write_bytes(led.read_bytes())
    led.unlink()
    led.symlink_to(real)
    assert _main.main(["decisions", "1001", "--sync"]) == 2
    assert "[x]" not in real.read_text(encoding="utf-8")


def test_sync_http_error_is_exit_1_and_writes_nothing(tmp_path, monkeypatch, capsys):
    md, html = _posted(S1)
    led, _ = _sync_env(tmp_path, monkeypatch, md, [], comment_responses=[AdoError("HTTP 500: SEKRET", status=500)])
    assert _main.main(["decisions", "1001", "--sync"]) == 1
    assert led.read_bytes() == md.encode("utf-8")


def test_resolve_refused_when_approvers_set(tmp_path, monkeypatch, capsys):
    m, fake, led = _env(tmp_path, monkeypatch, "\n".join([OPEN, VAL]))
    monkeypatch.setattr(m, "trusted", lambda repo=".", base=None: Trust([APPR], "comment", {}))
    _tty(monkeypatch)
    before = led.read_bytes()
    assert _resolve(m, "D1", "--validated") == 2 and _resolve(m, "D1", "--rejected", "--force") == 2
    err = capsys.readouterr().err
    assert "--sync" in err and "approvers" in err and led.read_bytes() == before and fake == {}
    _tty(monkeypatch, False)  # the same text without a terminal: the approvers rule comes first
    assert _resolve(m, "D1", "--validated") == 2 and "--sync" in capsys.readouterr().err
    monkeypatch.setattr(m, "trusted", lambda repo=".", base=None: Trust([], "terminal", {}))  # none configured: today's behaviour
    _tty(monkeypatch)
    assert _resolve(m, "D1", "--validated") == 0 and led.read_bytes() != before
    from ado.config import ConfigInvalid

    def boom(repo=".", base=None):
        raise ConfigInvalid("the target config is unreadable")
    monkeypatch.setattr(m, "trusted", boom)
    led.write_bytes(before)
    assert _resolve(m, "D1", "--validated") == 2 and led.read_bytes() == before  # unreadable target: fail closed


def test_sync_flag_combinations_exit_2(capsys):
    for extra in (["--post"], ["--apply"], ["--plan-out", "p.md"], ["--add", "x", "--kind", "decision"], ["--resolve", "D1", "--validated"], ["--if-any"],
                  ["--force"], ["--by", "PO"], ["--note", "n"], ["--validated"], ["--kind", "decision"], ["--supersedes", "D1"]):
        with pytest.raises(SystemExit) as e:
            _main.main(["decisions", "1001", "--sync", *extra])
        assert e.value.code == 2, extra
        capsys.readouterr()


# ---- P4-9 fix round 1 ----
def _edited_posting(html_old, html_new, modified=T_V2, same=False):
    """The kit's posting comment 10, edited once (version 2): what its versions endpoint says."""
    texts = (html_old, html_old if same else html_new)
    return _kit(html_new if not same else html_old, version=2, modifiedDate=modified), versions_response((1, texts[0], T_POST), (2, texts[1], modified))


def test_sync_skips_an_edited_posting_comment(cfg):
    old_md, old_html = _posted(S1)
    new_md, new_html = _posted(S1.replace("Use cursor paging", "Drop authentication"))  # the agent rewords D1, recomputes the marker, edits its own comment
    post, versions = _edited_posting(old_html, new_html)
    (new, res), _ = _sync(cfg, new_md, [post, _vote(11, "/approve D1", T_V1)], responses=[versions])
    assert new == new_md and res["applied"] == [] and res["skipped"] == {"D1": "posting-edited"}
    # a versions list that cannot be read is an edit too
    (new, res), _ = _sync(cfg, new_md, [post, _vote(11, "/approve D1", T_V1)], responses=[{"count": 0, "value": []}])
    assert res["skipped"] == {"D1": "posting-edited"}
    # the same edit by another identity
    other = dict(raw_comment(10, new_html, T_POST, author=KITID, modified_by=APPR, version=2, modifiedDate=T_V2))
    (new, res), _ = _sync(cfg, new_md, [other, _vote(11, "/approve D1", T_V1)], responses=[versions])
    assert res["skipped"] == {"D1": "posting-edited"}


def test_sync_a_line_reposted_after_an_edit_takes_only_a_newer_verdict(cfg):
    old_md, old_html = _posted(S1)
    new_md, new_html = _posted(S1.replace("Use cursor paging", "Drop authentication"), cid=20)
    post, versions = _edited_posting(old_html, new_html)
    base = [post, _kit(new_html, cid=20, date=T_V3)]
    (new, res), _ = _sync(cfg, new_md, base + [_vote(11, "/approve D1", T_V1)], responses=[versions])
    assert res["skipped"] == {"D1": "older-than-posting"} and new == new_md  # the repost (an unedited comment) is the posting; the old approval is older
    (new, res), _ = _sync(cfg, new_md, base + [_vote(12, "/approve D1", "2026-10-01T14:00:00Z")])
    assert res["applied"] == ["D1"] and decisions.parse_decisions(new)["validated"][0]["via"] == "12"


def test_sync_an_identical_re_edit_is_not_an_edit_but_the_verdict_must_follow_it(cfg):
    md, html = _posted(S1)
    post, versions = _edited_posting(html, html, modified=T_V2, same=True)
    (new, res), _ = _sync(cfg, md, [post, _vote(11, "/approve D1", T_V1)], responses=[versions])
    assert res["skipped"] == {"D1": "older-than-posting"}  # newer than the posting's creation, but older than its last modification
    (new, res), _ = _sync(cfg, md, [post, _vote(11, "/approve D1", T_V3)], responses=[versions])
    assert res["applied"] == ["D1"]


def test_sync_skips_a_line_the_posted_comment_line_cannot_show_exactly(cfg):
    long_text = "Use cursor paging " + "x" * 600 + " END"
    long_line = f"- [ ] D1: {long_text} → _(open)_ · kind: decision · why: w"
    md, html = _posted(long_line)
    assert "END" not in html  # cut at 500 characters
    changed = md.replace("END", "DIFFERENT")  # past character 499: the posted comment line is the same
    changed = changed.replace(" · posted: 10:" + decisions.line_hash(long_text), " · posted: 10:" + decisions.line_hash(long_text.replace("END", "DIFFERENT")))
    for body in (md, changed):
        (new, res), _ = _sync(cfg, body, [_kit(html), _vote(11, "/approve D1")])
        assert new == body and res["skipped"] == {"D1": "posting-lossy"}
    addr = "- [ ] D1: Mail dana@contoso.example about it → _(open)_ · kind: decision · why: w"  # a hand-written address: the comment says [email]
    md, html = _posted(addr)
    assert "[email]" in html
    swapped = md.replace("dana@contoso.example", "eve@fabrikam.example")
    swapped = swapped.replace("posted: 10:" + decisions.line_hash("Mail dana@contoso.example about it"), "posted: 10:" + decisions.line_hash("Mail eve@fabrikam.example about it"))
    for body in (md, swapped):
        (new, res), _ = _sync(cfg, body, [_kit(html), _vote(11, "/approve D1")])
        assert new == body and res["skipped"] == {"D1": "posting-lossy"}
    mention = _posted("- [ ] D1: Ask @dana first → _(open)_ · kind: decision · why: w")
    assert _sync(cfg, mention[0], [_kit(mention[1]), _vote(11, "/approve D1")])[0][1]["skipped"] == {"D1": "posting-lossy"}


def test_sync_refuses_when_a_synced_verdict_is_contradicted_but_cannot_be_rewritten(cfg):
    md, html = _posted(S1, S2)
    synced = _sync(cfg, md, [_kit(html), _vote(11, "/approve D1"), _vote(12, "/approve D2")])[0][0]
    later = [_kit(html), _vote(11, "/approve D1"), _vote(12, "/approve D2"), _vote(13, "/reject D1: no", T_V3)]
    hidden = next(ln for ln in synced.splitlines() if ln.startswith("- [x] D1"))
    for broken in (synced.replace(hidden, hidden + " <!-- x -->"),                               # (a) unwritable
                   synced.replace("Use cursor paging", "Use offset paging")):                       # (b) reworded, not reposted: `edited`
        with pytest.raises(ValueError, match=r"D1\b") as e:
            _sync(cfg, broken, later)
        assert "D2" not in str(e.value) and "cursor" not in str(e.value) and "nothing written" in str(e.value)
    # the same breakage with nothing contradicting the synced verdict is only a skip
    (new, res), _ = _sync(cfg, synced.replace("Use cursor paging", "Use offset paging"), [_kit(html), _vote(11, "/approve D1"), _vote(12, "/approve D2")])
    assert res["skipped"]["D1"] == "edited" and res["applied"] == []
    # an open line that is merely reworded or unposted is not an error: it stays open
    (new, res), _ = _sync(cfg, md.replace("Use cursor paging", "Use offset paging"), [_kit(html), _vote(11, "/reject D1")])
    assert res["skipped"]["D1"] == "edited"


def test_sync_verb_refuses_exit_2_and_writes_nothing_when_a_reject_cannot_land(tmp_path, monkeypatch, capsys):
    md, html = _posted(S1)
    synced = _sync_cfg_free(md, html)
    broken = synced + ""  # placeholder replaced below
    hidden = next(ln for ln in synced.splitlines() if ln.startswith("- [x] D1"))
    broken = synced.replace(hidden, hidden + " <!-- x -->")
    led, _ = _sync_env(tmp_path, monkeypatch, broken, [_kit(html), _vote(11, "/approve D1"), _vote(13, "/reject D1: no", T_V3)])
    assert _main.main(["decisions", "1001", "--sync"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "D1" in err and led.read_bytes() == broken.encode("utf-8")


def _sync_cfg_free(md, html):
    return decisions.sync(md, _rows(Config(organization="contoso", project="Fabrikam"), [_kit(html), _vote(11, "/approve D1")]), [APPR], {TOK, KITID},
                          FakeClient(Config(organization="contoso", project="Fabrikam")), 1001)[0]


def test_a_sync_that_validates_a_superseded_decision_prints_the_same_notice_as_resolve(cfg, tmp_path, monkeypatch, capsys):
    md, html = _posted(S1, "- [ ] D5: supersedes D1: Use offset paging → _(open)_ · kind: decision · why: w")
    (new, res), _ = _sync(cfg, md, [_kit(html), _vote(11, "/approve D1")])
    assert res["applied"] == ["D1"] and res["masked"] == {"D1": "D5"}
    led, _ = _sync_env(tmp_path, monkeypatch, md, [_kit(html), _vote(11, "/approve D1")])
    assert _main.main(["decisions", "1001", "--sync"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "D1 is superseded by D5: this verdict is masked while D5 stands (a rejected D5 would bring it back)" in out["notice"]
    assert _sync(cfg, md, [_kit(html)])[0][1]["masked"] == {}


def test_dod_compiles_without_a_warning():
    import warnings
    src = Path(decisions.__file__).with_name("dod.py")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(src.read_text(encoding="utf-8"), str(src), "exec")
