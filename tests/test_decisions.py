import json

from ado import decisions
from ado.__main__ import main
from ado.dod import SECTION2, render_dod, write_dod

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
