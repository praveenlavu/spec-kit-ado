"""P3-4b: the hierarchy walk and `ado rules <id>` (GETs only; every GET before any file; fixed texts; ids, counts, keys and hashes in JSON)."""
import json
import os
from pathlib import Path

import pytest

from ado import decisions, rules
from ado.client import AdoError
from ado.config import Config
from tests.conftest import FakeClient

UP = "System.LinkTypes.Hierarchy-Reverse"


def wi(wid, kind, html=None, parent=None, title="Points on purchases"):
    f = {"System.WorkItemType": kind, "System.Title": title}
    if html is not None:
        f["System.Description"] = html
    rel = [] if parent is None else [{"rel": UP, "url": f"https://dev.azure.com/contoso/{'0' * 8}-aaaa/_apis/wit/workItems/{parent}"}]
    return {"id": wid, "fields": f, "relations": rel}


def desc(*specs):
    """A Business rules section: (n, status) per rule, every label present (no warnings)."""
    return "<p>Business rules</p>" + "".join(
        f"<p>Rule {n}: Customers earn {n} points per euro.</p><p>Why: loyalty</p><p>Example: spend 10</p>"
        f"<p>Owner: Loyalty product owner</p><p>Status: {s}</p>" for n, s in specs)


STORY = wi(1001, "User Story", "<p>As a member</p>", parent=1201, title="Award points at checkout")
FEATURE = wi(1201, "Feature", desc((1, "approved"), (2, "draft")), parent=1200, title="Secret feature title")
EPIC = wi(1200, "Epic", desc((1, "approved"), (3, "retired")), title="Loyalty programme")
TYPES = ["Epic", "Feature"]


def cfg(**rules_cfg):
    c = Config(organization="contoso", project="Fabrikam")
    c.rules = {"enabled": True, "types": TYPES, **rules_cfg}
    return c


def env(tmp_path, monkeypatch, responses, rules_cfg=None, dod_path=None):
    from ado import __main__ as m
    data = {"organization": "contoso", "project": "Fabrikam"}
    if rules_cfg is not None:
        data["rules"] = rules_cfg
    if dod_path:
        data["dod_path"] = dod_path
    (tmp_path / "ado.config.json").write_text(json.dumps(data))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr(rules, "_today", lambda: "2026-11-03")
    box = {}

    def make(c):
        box["c"] = FakeClient(c, responses=list(responses))
        return box["c"]
    monkeypatch.setattr(m, "AdoClient", make)
    return m, box


def run(m, capsys, *args):
    code = m.main(["rules", *args])
    cap = capsys.readouterr()
    return code, (json.loads(cap.out) if cap.out.strip() else None), cap.err


def ran(box):
    return [(c["method"], c["path"]) for c in box["c"].calls]


def files(tmp_path):
    return sorted(p.name for p in tmp_path.rglob("Rules_*") if p.is_file())


# ---- walk ----

def test_walk_story_feature_epic():
    c = FakeClient(cfg(), [STORY, FEATURE, EPIC])
    got = rules.walk(c, 1001, TYPES)
    assert [(g["id"], g["type"]) for g in got] == [(1201, "Feature"), (1200, "Epic")]
    assert got[0]["work_item"] is FEATURE and got[0]["parsed"]["found"]
    assert [r["id"] for r in got[0]["parsed"]["rules"]] == ["BR-1201-1", "BR-1201-2"]
    assert [r["id"] for r in got[1]["parsed"]["rules"]] == ["BR-1200-1", "BR-1200-3"]
    assert [x["method"] for x in c.calls] == ["GET"] * 3
    assert [x["path"].split("/workitems/")[1].split("?")[0] for x in c.calls] == ["1001", "1201", "1200"]


def test_walk_skips_unlisted_types_and_ancestors_without_section():
    release = wi(1300, "Release", desc((1, "approved")), parent=1201)  # not in rules.types: walked through, never kept
    story = wi(1001, "User Story", "x", parent=1300)
    feature = wi(1201, "Feature", desc((1, "approved")), parent=1200)
    epic = wi(1200, "Epic", "<p>Just a description, no rules here.</p>")
    got = rules.walk(FakeClient(cfg(), [story, release, feature, epic]), 1001, TYPES)
    assert [g["id"] for g in got] == [1201]  # the Epic has no section: not in `from`
    got = rules.walk(FakeClient(cfg(), [story, release, feature, epic]), 1001, ["feature", "EPIC"])  # types are case-folded
    assert [g["id"] for g in got] == [1201]
    assert rules.walk(FakeClient(cfg(), [wi(1001, "Epic", desc((1, "approved")))]), 1001, TYPES) == []  # the story itself never counts
    no_desc = wi(1200, "Epic")  # no Description field at all
    assert rules.walk(FakeClient(cfg(), [wi(1001, "User Story", None, parent=1200), no_desc]), 1001, TYPES) == []


def test_walk_keeps_an_ancestor_whose_problem_would_otherwise_vanish():
    hidden = wi(1200, "Epic", "<!-- never closed " + desc((1, "approved")))  # the comment swallows the whole section
    got = rules.walk(FakeClient(cfg(), [wi(1001, "User Story", "x", parent=1200), hidden]), 1001, TYPES)
    assert [g["id"] for g in got] == [1200] and not got[0]["parsed"]["found"]
    assert [p["issue"] for p in got[0]["parsed"]["problems"]] == ["unclosed-markup"]


def test_walk_stops_at_3_links_and_on_cycle():
    chain = [wi(1001, "User Story", "x", parent=1100)] + [wi(1100 + i, "Epic", desc((1, "approved")), parent=1101 + i) for i in range(5)]
    c = FakeClient(cfg(), chain)
    got = rules.walk(c, 1001, TYPES)
    assert [g["id"] for g in got] == [1100, 1101, 1102] and len(c.calls) == 4  # story + 3 links; the fourth is never fetched
    loop = [wi(1001, "User Story", "x", parent=1200), wi(1200, "Feature", desc((1, "approved")), parent=1001)]
    c = FakeClient(cfg(), loop)
    assert [g["id"] for g in rules.walk(c, 1001, TYPES)] == [1200] and len(c.calls) == 2  # the parent that is the story: stop
    self_loop = [wi(1001, "User Story", "x", parent=1200), wi(1200, "Feature", desc((1, "approved")), parent=1200)]
    c = FakeClient(cfg(), self_loop)
    assert [g["id"] for g in rules.walk(c, 1001, TYPES)] == [1200] and len(c.calls) == 2


@pytest.mark.parametrize("url", [
    "https://dev.azure.com/contoso/p/_apis/wit/workItems/abc", "https://dev.azure.com/contoso/p/_apis/wit/workItems/",
    "https://dev.azure.com/contoso/p/_apis/wit/workItems/1200?x=1", "https://dev.azure.com/contoso/p/_apis/wit/workItems/1234567890",
    "https://dev.azure.com/contoso/p/_apis/wit/workItems/..", "https://dev.azure.com/contoso/p/_apis/wit/workItems/-5",
    "https://dev.azure.com/contoso/p/_apis/wit/workItems/12\n", "https://dev.azure.com/contoso/p/_apis/wit/workItems/١٢",
    "https://dev.azure.com/contoso/p/_apis/wit/workItems/0", "", None, 12, ["1200"],
])
def test_walk_bad_parent_url_stops(url):
    story = {"id": 1001, "fields": {"System.WorkItemType": "User Story"}, "relations": [{"rel": UP, "url": url}]}
    c, problems = FakeClient(cfg(), [story, EPIC]), []
    assert rules.walk(c, 1001, TYPES, problems) == [] and len(c.calls) == 1  # nothing else is fetched
    assert problems == [{"id": 1001, "rule": None, "level": "error", "issue": "bad-parent-link"}]
    assert "bad-parent-link" in rules.WALK_ISSUES


def test_walk_two_parents_or_an_id_that_does_not_match_stop():
    two = {"id": 1001, "fields": {}, "relations": wi(1, "x", parent=1200)["relations"] + wi(1, "x", parent=1201)["relations"]}
    c, problems = FakeClient(cfg(), [two, EPIC]), []
    assert rules.walk(c, 1001, TYPES, problems) == [] and len(c.calls) == 1 and problems[0]["issue"] == "bad-parent-link"
    liar = dict(EPIC, id=1999)  # ADO answered with another work item than the link names
    c, problems = FakeClient(cfg(), [STORY, dict(FEATURE, id=1999)]), []
    assert rules.walk(c, 1001, TYPES, problems) == [] and problems[0]["issue"] == "bad-parent-link"
    assert liar["id"] == 1999
    odd = [{"rel": "System.LinkTypes.Hierarchy-Forward", "url": "x"}, "not a dict", {"rel": UP, "url": "https://x/_apis/wit/workItems/1200"}]
    story = {"id": 1001, "fields": {}, "relations": odd}
    assert [g["id"] for g in rules.walk(FakeClient(cfg(), [story, EPIC]), 1001, TYPES)] == [1200]


# ---- the verb ----

def test_get_only(tmp_path, monkeypatch, capsys):
    m, box = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, _ = run(m, capsys, "1001")
    assert code == 0 and files(tmp_path) == ["Rules_1200.md", "Rules_1201.md"]
    assert ran(box) == [("GET", f"/Fabrikam/_apis/wit/workitems/{i}?$expand=relations&api-version=7.1") for i in (1001, 1201, 1200)]
    assert all(c["method"] == "GET" and c["body"] is None for c in box["c"].calls)


def test_json_shape_and_files(tmp_path, monkeypatch, capsys):
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, _ = run(m, capsys, "1001")
    assert code == 0 and list(out) == ["from", "files", "rules", "problems", "changed", "decisions"]
    assert out["from"] == [1201, 1200] and out["changed"] == [1201, 1200] and out["decisions"] == []
    assert out["files"] == [str(Path("dod/Rules_1201.md")), str(Path("dod/Rules_1200.md"))]
    assert out["rules"] == {"approved": 2, "draft": 1, "retired": 1}
    assert out["problems"] == []
    text = (tmp_path / "dod" / "Rules_1201.md").read_text(encoding="utf-8")
    pf = rules.parse_file(text, 1201)  # the toolkit's own file reads back, with the walk's rules in it
    assert not pf["invalid"] and pf["format"] == rules.FORMAT and sorted(pf["rules"]) == ["BR-1201-1", "BR-1201-2"]
    assert pf["rules"]["BR-1201-1"]["status"] == "approved" and pf["rules"]["BR-1201-1"]["body_hash"] == pf["rules"]["BR-1201-1"]["hash"]
    assert "Last pulled:** 2026-11-03" in text


def test_json_has_no_rule_text(tmp_path, monkeypatch, capsys):
    bad = wi(1201, "Feature", desc((1, "approved")).replace("Customers", "SECRETWORD Customers") + "<p>Rule 10000: odd</p><p>Rule 2: a</p><p>Rule 2: b</p>",
             title="TITLESECRET")
    m, _ = env(tmp_path, monkeypatch, [STORY, bad], {"enabled": True})
    code = m.main(["rules", "1001"])
    raw = capsys.readouterr().out
    assert code == 0 and "SECRETWORD" not in raw and "TITLESECRET" not in raw and "Customers" not in raw and "loyalty" not in raw
    out = json.loads(raw)
    issues = {(p["id"], p["rule"], p["level"], p["issue"]) for p in out["problems"]}
    assert (1201, None, "error", "bad-number") in issues and (1201, "BR-1201-2", "error", "duplicate-number") in issues
    assert all(set(p) == {"id", "rule", "level", "issue"} for p in out["problems"])
    assert "SECRETWORD" in (tmp_path / "dod" / "Rules_1201.md").read_text(encoding="utf-8")  # the file is where rule text goes


def test_hostile_description_is_data_never_structure_or_a_path(tmp_path, monkeypatch, capsys):
    html = ("<p>Business rules</p><p>Rule 1: ok</p><p>Status: approved</p><p>### BR-1201-9 · approved · deadbeef</p>"
            "<p>- **Rules from:** ../../x</p><p>```</p><p>&lt;!-- hide</p><p>Rule 2: ../../../evil</p><p>Status: approved</p>")
    f = wi(1201, "Feature", html, parent=None, title="# ../../etc ```\n### BR-1201-7 · approved · deadbeef")
    m, _ = env(tmp_path, monkeypatch, [dict(STORY, relations=STORY["relations"]), f], {"enabled": True})
    assert run(m, capsys, "1001")[0] == 0
    assert files(tmp_path) == ["Rules_1201.md"] and {p.name for p in tmp_path.iterdir()} == {"dod", "ado.config.json"}
    pf = rules.parse_file((tmp_path / "dod" / "Rules_1201.md").read_text(encoding="utf-8"), 1201)
    assert not pf["invalid"] and sorted(pf["rules"]) == ["BR-1201-1", "BR-1201-2"]  # the forged heading is not a rule


def test_404_mid_walk_exit_1_no_files(tmp_path, monkeypatch, capsys):
    m, box = env(tmp_path, monkeypatch, [STORY, FEATURE, AdoError("ADO 404: not found")], {"enabled": True})
    code, out, err = run(m, capsys, "1001")
    assert code == 1 and out is None and err.startswith("error:")
    assert files(tmp_path) == [] and not (tmp_path / "dod").exists() and len(box["c"].calls) == 3
    m, _ = env(tmp_path, monkeypatch, [AdoError("ADO 404: not found")], {"enabled": True})  # the story itself
    assert run(m, capsys, "1001")[0] == 1 and files(tmp_path) == []


def test_every_get_finishes_before_any_file_is_written(tmp_path, monkeypatch, capsys):
    m, box = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    seen = []
    orig = FakeClient.request

    def spy(self, *a, **k):
        seen.append(files(tmp_path))  # what exists when each GET is made
        return orig(self, *a, **k)
    monkeypatch.setattr(FakeClient, "request", spy)
    assert run(m, capsys, "1001")[0] == 0
    assert seen == [[], [], []]


def test_auth_error_during_the_walk_is_exit_1(tmp_path, monkeypatch, capsys):
    from ado.auth import AuthError
    m, _ = env(tmp_path, monkeypatch, [STORY, AuthError("no credential")], {"enabled": True})
    assert run(m, capsys, "1001")[0] == 1 and files(tmp_path) == []


def test_bad_parent_link_is_a_problem_and_the_ancestors_found_so_far_are_pulled(tmp_path, monkeypatch, capsys):
    bad = wi(1201, "Feature", desc((1, "approved")))
    bad["relations"] = [{"rel": UP, "url": "https://dev.azure.com/contoso/p/_apis/wit/workItems/12ab"}]
    m, box = env(tmp_path, monkeypatch, [STORY, bad], {"enabled": True})
    code, out, _ = run(m, capsys, "1001")
    assert code == 0 and out["from"] == [1201] and len(box["c"].calls) == 2
    assert {"id": 1201, "rule": None, "level": "error", "issue": "bad-parent-link"} in out["problems"]
    assert files(tmp_path) == ["Rules_1201.md"]


def test_no_ancestor_with_a_section_writes_nothing(tmp_path, monkeypatch, capsys):
    m, _ = env(tmp_path, monkeypatch, [dict(STORY, relations=[])], {"enabled": True})
    code, out, _ = run(m, capsys, "1001")
    assert code == 0 and out == {"from": [], "files": [], "rules": {"approved": 0, "draft": 0, "retired": 0}, "problems": [], "changed": [], "decisions": []}
    assert files(tmp_path) == [] and not (tmp_path / "dod").exists()


def test_rerun_without_ado_change_rewrites_nothing(tmp_path, monkeypatch, capsys):
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    assert run(m, capsys, "1001")[0] == 0
    f1, f2 = tmp_path / "dod" / "Rules_1201.md", tmp_path / "dod" / "Rules_1200.md"
    first = (f1.read_bytes(), f2.read_bytes())
    ino = (f1.stat().st_ino, f2.stat().st_ino)
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    monkeypatch.setattr(rules, "_today", lambda: "2027-01-09")  # another day: only the `pulled` stamp would differ
    code, out, _ = run(m, capsys, "1001")
    assert code == 0 and out["changed"] == [] and out["from"] == [1201, 1200] and len(out["files"]) == 2
    assert (f1.read_bytes(), f2.read_bytes()) == first and (f1.stat().st_ino, f2.stat().st_ino) == ino  # not even replaced
    assert b"2026-11-03" in first[0] and b"2027-01-09" not in first[0]
    # ADO changed one rule: that file is rewritten, with the new date; the other is left alone
    feature = wi(1201, "Feature", desc((1, "approved"), (2, "approved")), parent=1200)
    m, _ = env(tmp_path, monkeypatch, [STORY, feature, EPIC], {"enabled": True})
    monkeypatch.setattr(rules, "_today", lambda: "2027-01-09")
    code, out, _ = run(m, capsys, "1001")
    assert out["changed"] == [1201] and f2.read_bytes() == first[1] and b"2027-01-09" in f1.read_bytes()
    assert out["rules"] == {"approved": 3, "draft": 0, "retired": 1}


def test_a_hand_edited_file_is_replaced_and_crlf_is_kept(tmp_path, monkeypatch, capsys):
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    run(m, capsys, "1001")
    f = tmp_path / "dod" / "Rules_1201.md"
    good = f.read_bytes()
    f.write_bytes(good.replace(b"approved \xc2\xb7", b"retired \xc2\xb7", 1))  # a PR edit
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, _ = run(m, capsys, "1001")
    assert out["changed"] == [1201] and f.read_bytes() == good
    f.write_bytes(good.replace(b"\n", b"\r\n"))
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, _ = run(m, capsys, "1001")
    assert out["changed"] == [] and f.read_bytes() == good.replace(b"\n", b"\r\n")  # same content, its own terminator: untouched


def test_existing_symlink_or_non_file_is_refused_and_nothing_is_written(tmp_path, monkeypatch, capsys):
    (tmp_path / "dod").mkdir()
    (tmp_path / "elsewhere.md").write_text("keep me")
    os.symlink(tmp_path / "elsewhere.md", tmp_path / "dod" / "Rules_1200.md")
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, err = run(m, capsys, "1001")
    assert code == 2 and out is None and err.startswith("error:") and "elsewhere" not in err
    assert (tmp_path / "elsewhere.md").read_text() == "keep me" and files(tmp_path) == ["Rules_1200.md"]  # no Rules_1201.md either
    assert (tmp_path / "dod" / "Rules_1200.md").is_symlink()
    (tmp_path / "dod" / "Rules_1200.md").unlink()
    (tmp_path / "dod" / "Rules_1200.md").mkdir()  # a directory in the way
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    assert run(m, capsys, "1001")[0] == 2 and not (tmp_path / "dod" / "Rules_1201.md").exists()


def test_unwritable_dod_path_exit_2(tmp_path, monkeypatch, capsys):
    (tmp_path / "blocked").write_text("a file where the directory should be")
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True}, dod_path="blocked")
    code, out, err = run(m, capsys, "1001")
    assert code == 2 and out is None and err.startswith("error:")
    assert (tmp_path / "blocked").read_text() == "a file where the directory should be"


def test_feature_decisions_listed_with_counts(tmp_path, monkeypatch, capsys):
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "Decisions_1201.md").write_text(
        decisions.render_file(1201)
        + "- [ ] D1: Ask the PO → _(open)_ · kind: decision · why: w\n- [ ] D2: Second → _(open)_ · kind: assumption · why: w\n"
        + "- [x] D3: Skip audit → rejected · by: PO · note: no\n- [x] D4: Cap → validated · by: PO · note: ok\n", encoding="utf-8")
    (tmp_path / "dod" / "Decisions_1001.md").write_text(decisions.render_file(1001) + "- [ ] D1: a story one → _(open)_ · kind: decision · why: w\n")
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    code, out, _ = run(m, capsys, "1001")
    assert code == 0 and out["decisions"] == [{"id": 1201, "open": 2, "rejected": 1}]  # only a kept ancestor's file; none for the Epic
    assert "Ask the PO" not in json.dumps(out)
    # a symlinked decisions file is refused before anything is written
    (tmp_path / "dod" / "Decisions_1200.md").symlink_to(tmp_path / "dod" / "Decisions_1201.md")
    for p in tmp_path.glob("dod/Rules_*"):
        p.unlink()
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    assert run(m, capsys, "1001")[0] == 2 and files(tmp_path) == []


def test_ado_decisions_add_and_post_work_for_a_feature_id(tmp_path, monkeypatch, capsys):
    """R§13 AC 9: the existing verbs take any work item id; the comment goes to the Feature, as for a story."""
    from ado import __main__ as m
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    box = {}
    monkeypatch.setattr(m, "AdoClient", lambda c: box.setdefault("c", FakeClient(c, responses=[{"id": 555}])))
    assert m.main(["decisions", "1201", "--add", "Points expire after 12 months", "--kind", "decision", "--why", "Loyalty product owner said so"]) == 0
    capsys.readouterr()
    assert (tmp_path / "dod" / "Decisions_1201.md").exists()
    assert m.main(["decisions", "1201", "--post", "--plan-out", "p.md"]) == 0
    assert "Points expire after 12 months" in (tmp_path / "p.md").read_text(encoding="utf-8")
    assert [c["method"] for c in box["c"].calls] == []  # the preview makes no request at all
    capsys.readouterr()
    assert m.main(["decisions", "1201", "--post", "--apply", "--expect-plan", "p.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    posts = [c for c in box["c"].calls if c["method"] == "POST"]
    assert len(posts) == 1 and posts[0]["path"].startswith("/Fabrikam/_apis/wit/workItems/1201/comments")
    assert out["id"] == 1201 and out["comment_id"] == 555 and out["lines"] == ["D1"]
    assert decisions.parse_decisions((tmp_path / "dod" / "Decisions_1201.md").read_text(encoding="utf-8"))["open"][0]["posted"]["comment_id"] == 555


# ---- off, exit codes ----

@pytest.mark.parametrize("rules_cfg", [None, {"enabled": False}, {"types": ["Epic"]}])
def test_works_whatever_rules_enabled_says(tmp_path, monkeypatch, capsys, rules_cfg):
    m, box = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], rules_cfg)
    code, out, _ = run(m, capsys, "1001")
    types = (rules_cfg or {}).get("types", TYPES)
    want = [1200] if types == ["Epic"] else [1201, 1200]  # the default types (Epic, Feature) apply when `rules` has none
    assert code == 0 and out["from"] == want and files(tmp_path) == sorted(f"Rules_{i}.md" for i in want)
    assert all(c["method"] == "GET" for c in box["c"].calls)


def test_two_ancestors_problems_can_be_told_apart(tmp_path, monkeypatch, capsys):
    both = [wi(1201, "Feature", desc((1, "approved")) + "<p>Rule 10000: x</p>", parent=1200), wi(1200, "Epic", desc((1, "approved")) + "<p>Rule 10000: x</p>")]
    m, _ = env(tmp_path, monkeypatch, [STORY, *both], {"enabled": True})
    out = run(m, capsys, "1001")[1]
    assert [(p["id"], p["issue"]) for p in out["problems"]] == [(1201, "bad-number"), (1200, "bad-number")]


def test_the_storys_own_answered_id_must_match(tmp_path, monkeypatch, capsys):
    with pytest.raises(AdoError):
        rules.walk(FakeClient(cfg(), [dict(STORY, id=1002), FEATURE]), 1001, TYPES)
    m, box = env(tmp_path, monkeypatch, [dict(STORY, id=1002), FEATURE], {"enabled": True})
    code, out, err = run(m, capsys, "1001")
    assert code == 1 and out is None and err.startswith("error:") and files(tmp_path) == [] and len(box["c"].calls) == 1


def test_cli_rules_exit_codes(tmp_path, monkeypatch, capsys):
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    assert m.main(["rules", "1001"]) == 0
    capsys.readouterr()
    for argv in (["rules"], ["rules", "abc"], ["rules", "0"], ["rules", "-3"], ["rules", "1000000000"], ["rules", "1001", "--confirm"]):
        with pytest.raises(SystemExit) as e:
            m.main(argv)
        assert e.value.code == 2, argv
    capsys.readouterr()
    m, _ = env(tmp_path, monkeypatch, [AdoError("ADO 500: down")], {"enabled": True})
    assert m.main(["rules", "1001"]) == 1
    assert "error:" in capsys.readouterr().err


def test_main_help_lists_the_verb(capsys):
    from ado.__main__ import main
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "rules" in capsys.readouterr().out
