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
    for argv in (["rules"], ["rules", "abc"], ["rules", "0"], ["rules", "-3"], ["rules", "1000000000"], ["rules", "1001", "--bogus"]):
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


# ---- `ado rules <id> --confirm` (P3-7): the hashes a human reviewed go into the ledger header; local only ----

import hashlib  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

from ado import dod, stack  # noqa: E402
from tests.conftest import TypedStdin, run_in_pty, shown_code, terminal  # noqa: E402

FROM_LINE = "- **Rules from:** 1201, 1200\n"
CMD = ["rules", "1001", "--confirm"]


class Ctx:
    """A pulled repo (Rules_1201.md, Rules_1200.md) and a ledger with `Rules from: 1201, 1200` in `tmp_path`."""

    def __init__(self, tmp_path, m):
        self.root, self.m = tmp_path, m
        self.ledger = tmp_path / "dod" / "DoD_1001.md"
        self.h = {r: v["hash"] for w in (1200, 1201) for r, v in rules.parse_file(self.rules_text(w), w)["rules"].items()}

    def rules_text(self, wid):
        return (self.root / "dod" / f"Rules_{wid}.md").read_text(encoding="utf-8")

    def text(self):
        return self.ledger.read_bytes().decode("utf-8")

    def put(self, md):
        self.ledger.write_bytes(md.encode("utf-8"))

    def record(self, value, eol="\n"):
        """Hand-write a `Rules reviewed:` line under `Rules from:` (as an earlier --confirm would have)."""
        md = self.text()
        assert FROM_LINE in md
        self.put(md.replace(FROM_LINE, FROM_LINE + f"- **Rules reviewed:** {value}\n"))

    def value(self):
        return self.text().split("- **Rules reviewed:** ")[1].split("\n")[0].rstrip("\r")

    def want(self):
        return f"BR-1200-1@{self.h['BR-1200-1']}, BR-1201-1@{self.h['BR-1201-1']}"

    def run(self, capsys, *extra):
        code = self.m.main([*CMD, *extra])
        cap = capsys.readouterr()
        return code, (json.loads(cap.out) if cap.out.strip().startswith("{") else None), cap.out, cap.err


@pytest.fixture
def ctx(tmp_path, monkeypatch, capsys):
    m, _ = env(tmp_path, monkeypatch, [STORY, FEATURE, EPIC], {"enabled": True})
    assert m.main(["rules", "1001"]) == 0
    capsys.readouterr()
    dod.write_dod(STORY, "dod", "contoso", "Fabrikam", rules_from=[1201, 1200])

    def boom(c):
        raise AssertionError("--confirm must not reach ADO")
    monkeypatch.setattr(m, "AdoClient", boom)
    return Ctx(tmp_path, m)


def leftovers(ctx):
    return sorted(p.name for p in (ctx.root / "dod").iterdir() if p.name.endswith(".tmp"))


def test_first_confirm_without_tty_writes(ctx, capsys):
    before = ctx.text()
    code, out, raw, err = ctx.run(capsys)
    assert code == 0 and err == ""
    assert ctx.value() == ctx.want() and ctx.want().startswith("BR-1200-1@")  # sorted by work item, then number; the draft BR-1201-2 and the retired BR-1200-3 are not in scope
    assert ctx.text() == before.replace(FROM_LINE, FROM_LINE + f"- **Rules reviewed:** {ctx.want()}\n")
    assert dod.ledger_rules_reviewed(ctx.text()) == {"present": True, "valid": True, "hashes": {"BR-1200-1": ctx.h["BR-1200-1"], "BR-1201-1": ctx.h["BR-1201-1"]}}
    assert out == {"id": 1001, "reviewed": ctx.want(), "added": ["BR-1200-1", "BR-1201-1"], "changed": [], "removed": [], "written": True}
    assert "Customers earn" not in raw and leftovers(ctx) == []


def test_confirm_is_a_no_op_when_nothing_changed(ctx, capsys):
    assert ctx.run(capsys)[0] == 0
    first = ctx.ledger.stat()
    code, out, _, _ = ctx.run(capsys)
    assert code == 0 and out["written"] is False and out["added"] == out["changed"] == out["removed"] == []
    assert ctx.ledger.stat().st_ino == first.st_ino and ctx.ledger.stat().st_mtime_ns == first.st_mtime_ns


def test_a_recorded_line_in_another_order_is_not_a_change(ctx, capsys):
    ctx.record(f"BR-1201-1@{ctx.h['BR-1201-1']}, BR-1200-1@{ctx.h['BR-1200-1']}")
    before = ctx.text()
    code, out, _, _ = ctx.run(capsys)
    assert code == 0 and out["written"] is False and ctx.text() == before


def test_new_ids_are_added_without_a_terminal_and_the_old_ones_kept(ctx, capsys):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}")
    code, out, _, _ = ctx.run(capsys)
    assert code == 0 and out["added"] == ["BR-1201-1"] and out["changed"] == []
    assert ctx.value() == ctx.want()


def test_changed_hash_refused_without_tty(ctx, capsys):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@aaaaaaaa")
    before = ctx.text()
    code, out, raw, err = ctx.run(capsys)
    assert code == 2 and out is None and raw == ""
    assert err == f"error: {rules.CONFIRM_TERMINAL}\n" and ctx.text() == before
    assert "aaaaaaaa" not in err and ctx.h["BR-1201-1"] not in err and leftovers(ctx) == []


def test_changed_hash_with_tty_rewrites_only_that_line(ctx, capsys, monkeypatch):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@aaaaaaaa")
    before = ctx.text()
    stdin = terminal(monkeypatch)
    code, out, raw, err = ctx.run(capsys)
    assert code == 0 and err == "" and stdin.lines == 1
    assert "BR-1201-1" in raw and "aaaaaaaa" in raw and ctx.h["BR-1201-1"] in raw and "Customers earn" not in raw
    after = ctx.text()
    assert after == before.replace("BR-1201-1@aaaaaaaa", f"BR-1201-1@{ctx.h['BR-1201-1']}")
    assert after.count("\n") == before.count("\n") and leftovers(ctx) == []
    assert json.loads(raw[raw.index("{"):]) == {"id": 1001, "reviewed": ctx.want(), "added": [], "changed": ["BR-1201-1"], "removed": [], "written": True}


def test_dropping_a_recorded_id_needs_the_terminal_too(ctx, capsys, monkeypatch):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1200-2@bbbbbbbb, BR-1201-1@{ctx.h['BR-1201-1']}")  # BR-1200-2 is in no file
    before = ctx.text()
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_TERMINAL}\n" and ctx.text() == before
    terminal(monkeypatch)
    code, out, raw, _ = ctx.run(capsys)
    assert code == 0 and "BR-1200-2" in raw and "bbbbbbbb" in raw and out is None  # the preview comes first, the JSON after it
    assert ctx.value() == ctx.want()


def test_an_excluded_rule_leaves_the_record_only_through_the_terminal(ctx, capsys, monkeypatch):
    md, d = decisions.add(decisions.render_file(1001), "decision", "out of scope: BR-1201-1", why="not for this story")
    d1 = decisions.parse_decisions(md)["open"][0]
    (ctx.root / "dod" / "Decisions_1001.md").write_text(md.rstrip("\n") + f" · posted: 5:{decisions.marker_hash(d1)}\n", encoding="utf-8")
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@{ctx.h['BR-1201-1']}")
    assert ctx.run(capsys)[0] == 2
    terminal(monkeypatch)
    code, _, raw, _ = ctx.run(capsys)
    assert code == 0 and ctx.value() == f"BR-1200-1@{ctx.h['BR-1200-1']}"


def test_nothing_in_scope_records_none(ctx, capsys):
    ctx.put(ctx.text().replace(FROM_LINE, "- **Rules from:** none\n"))
    code, out, _, _ = ctx.run(capsys)
    assert code == 0 and ctx.value() == "none" and out["reviewed"] == "none"
    assert dod.ledger_rules_reviewed(ctx.text())["valid"]


def test_confirm_survives_refresh(ctx, capsys):
    assert ctx.run(capsys)[0] == 0
    line = f"- **Rules reviewed:** {ctx.want()}\n"
    dod.write_dod(STORY, "dod", "contoso", "Fabrikam", refresh=True, rules_from=[1201, 1200])
    assert line in ctx.text() and ctx.text().count("Rules reviewed") == 1
    dod.write_dod(STORY, "dod", "contoso", "Fabrikam", refresh=True)  # rules off: the line is kept too
    assert line in ctx.text()


def test_missing_ledger_exit_2(ctx, capsys):
    ctx.ledger.unlink()
    code, out, raw, err = ctx.run(capsys)
    assert code == 2 and out is None and err == f"error: {rules.CONFIRM_NO_LEDGER}\n" and not ctx.ledger.exists()


def test_crlf_ledger_keeps_its_terminator_on_insert_and_on_replace(ctx, capsys, monkeypatch):
    ctx.put(ctx.text().replace("\n", "\r\n"))
    before = ctx.ledger.read_bytes()
    assert ctx.run(capsys)[0] == 0
    after = ctx.ledger.read_bytes()
    assert after == before.replace(b"- **Rules from:** 1201, 1200\r\n", b"- **Rules from:** 1201, 1200\r\n- **Rules reviewed:** " + ctx.want().encode() + b"\r\n")
    assert after.count(b"\n") == after.count(b"\r\n")
    ctx.put(ctx.text().replace(f"BR-1201-1@{ctx.h['BR-1201-1']}", "BR-1201-1@aaaaaaaa"))
    before = ctx.ledger.read_bytes()
    terminal(monkeypatch)
    assert ctx.run(capsys)[0] == 0
    assert ctx.ledger.read_bytes() == before.replace(b"BR-1201-1@aaaaaaaa", f"BR-1201-1@{ctx.h['BR-1201-1']}".encode())
    assert ctx.ledger.read_bytes().count(b"\n") == ctx.ledger.read_bytes().count(b"\r\n")


def test_refresh_after_a_crlf_confirm_keeps_the_line(ctx, capsys):
    ctx.put(ctx.text().replace("\n", "\r\n"))
    assert ctx.run(capsys)[0] == 0
    dod.write_dod(STORY, "dod", "contoso", "Fabrikam", refresh=True, rules_from=[1201, 1200])
    assert f"- **Rules reviewed:** {ctx.want()}\n" in ctx.text()


def test_a_symlinked_ledger_is_refused_and_nothing_is_written(ctx, capsys):
    real = ctx.root / "real_ledger.md"
    real.write_bytes(ctx.ledger.read_bytes())
    ctx.ledger.unlink()
    ctx.ledger.symlink_to(real)
    before = real.read_bytes()
    code, out, raw, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_LEDGER}\n" and real.read_bytes() == before and ctx.ledger.is_symlink() and leftovers(ctx) == []


def test_a_hard_linked_or_non_regular_ledger_is_refused(ctx, capsys):
    os.link(ctx.ledger, ctx.root / "other_name.md")
    assert ctx.run(capsys)[0] == 2 and "Rules reviewed" not in ctx.text()
    os.unlink(ctx.root / "other_name.md")
    ctx.ledger.unlink()
    ctx.ledger.mkdir()
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_LEDGER}\n"


def test_a_rule_whose_text_no_longer_matches_its_heading_hash_is_not_recordable(ctx, capsys):
    path = ctx.root / "dod" / "Rules_1201.md"
    path.write_text(path.read_text(encoding="utf-8").replace("Customers earn 1 points per euro.", "Customers earn 100 points per euro."), encoding="utf-8")
    before = ctx.text()
    code, out, raw, err = ctx.run(capsys)
    assert code == 2 and raw == "" and ctx.text() == before
    assert err == "error: " + rules.CONFIRM_HASH.format(ids="BR-1201-1") + "\n" and "100 points" not in err


def test_an_invalid_or_missing_rules_file_is_refused(ctx, capsys):
    before = ctx.text()
    path = ctx.root / "dod" / "Rules_1200.md"
    good = path.read_text(encoding="utf-8")
    path.write_text(good + "\n<!-- hide -->\n", encoding="utf-8")
    assert ctx.run(capsys)[3] == f"error: {rules.CONFIRM_FILES}\n"
    path.write_text(good.replace("rules-format: 1", "rules-format: 2"), encoding="utf-8")
    assert ctx.run(capsys)[3] == f"error: {rules.CONFIRM_FILES}\n"
    path.unlink()
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_FILES}\n" and ctx.text() == before


def test_a_symlinked_rules_file_is_refused(ctx, capsys):
    path = ctx.root / "dod" / "Rules_1200.md"
    real = ctx.root / "copy.md"
    real.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(real)
    assert ctx.run(capsys)[3] == f"error: {rules.CONFIRM_FILES}\n" and "Rules reviewed" not in ctx.text()


def test_an_unreadable_decisions_file_is_refused(ctx, capsys):
    (ctx.root / "dod" / "Decisions_1001.md").mkdir()
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_DECISIONS}\n" and "Rules reviewed" not in ctx.text()


@pytest.mark.parametrize("name,edit", [
    ("opener in the title hides the first heading", lambda md: md.replace("# DoD — US1001: ", "# DoD — US1001: x <!-- ", 1)),
    ("a fence hides the first heading", lambda md: md.replace("\n- **ADO:**", "\n```\n- **ADO:**", 1)),
    ("two Rules reviewed lines", lambda md: md.replace(FROM_LINE, FROM_LINE + "- **Rules reviewed:** none\n- **Rules reviewed:** none\n")),
    ("a malformed Rules reviewed line", lambda md: md.replace(FROM_LINE, FROM_LINE + "- **Rules reviewed:** BR-1201-1 deadbeef\n")),
    ("a quoted Rules reviewed line", lambda md: md.replace(FROM_LINE, FROM_LINE + "> - **Rules reviewed:** none\n")),
    ("a repeated id", lambda md: md.replace(FROM_LINE, FROM_LINE + "- **Rules reviewed:** BR-1201-1@aaaaaaaa, BR-1201-1@bbbbbbbb\n")),
    ("a malformed Rules from line", lambda md: md.replace(FROM_LINE, "- **Rules from:** 1201,1200\n")),
    ("two Rules from lines", lambda md: md.replace(FROM_LINE, FROM_LINE + FROM_LINE)),
])
def test_an_untrusted_or_malformed_header_is_refused(ctx, capsys, monkeypatch, name, edit):
    ctx.put(edit(ctx.text()))
    before = ctx.ledger.read_bytes()
    for tty in (False, True):
        if tty:
            terminal(monkeypatch)
        code, out, raw, err = ctx.run(capsys)
        assert code == 2 and err == f"error: {rules.CONFIRM_HEADER}\n" and ctx.ledger.read_bytes() == before, name
    assert leftovers(ctx) == []


def test_a_ledger_without_rules_from_is_refused(ctx, capsys):
    ctx.put(ctx.text().replace(FROM_LINE, ""))
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_NO_FROM}\n" and "Rules reviewed" not in ctx.text()


def test_a_forged_line_in_section_1_is_not_the_header(ctx, capsys):
    ctx.put(ctx.text().replace("### Description\n", "### Description\n- **Rules reviewed:** BR-1201-1@aaaaaaaa\n"))
    code, out, _, _ = ctx.run(capsys)
    assert code == 0 and out["added"] == ["BR-1200-1", "BR-1201-1"] and ctx.value() == ctx.want()


def test_adoconfig_set_is_refused(ctx, capsys, monkeypatch, tmp_path_factory):
    other = tmp_path_factory.mktemp("elsewhere") / "c.json"
    other.write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", "dod_path": "elsewhere"}))
    monkeypatch.setenv("ADO_CONFIG", str(other))
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_ENV}\n" and "Rules reviewed" not in ctx.text()


def test_the_confirm_code_covers_the_ledger_and_the_value(ctx):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@aaaaaaaa")
    a = rules.prepare_confirm("dod", 1001)
    assert a["needs_human"] and a["changed"] == ["BR-1201-1"] and a["code"] == rules.prepare_confirm("dod", 1001)["code"]
    ctx.put(ctx.text().replace("Last synced:** ", "Last synced:** x"))
    assert rules.prepare_confirm("dod", 1001)["code"] != a["code"]


# ---- the typed approval code (the same mechanism as `ado stack approve`) ----

def stale(ctx):
    ctx.record(f"BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@aaaaaaaa")
    return ctx.ledger.read_bytes()


@pytest.mark.parametrize("answer", ["deadbeef0000\n", "\n", "", "yes\n", KeyboardInterrupt])
def test_a_wrong_or_missing_code_confirms_nothing(ctx, capsys, monkeypatch, answer):
    before = stale(ctx)
    terminal(monkeypatch, answer)
    code, out, raw, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {stack.E_NOT_CONFIRMED}\n" and ctx.ledger.read_bytes() == before and leftovers(ctx) == []


def test_the_code_is_never_read_from_a_pipe(ctx, capsys, monkeypatch):
    before = stale(ctx)

    class Piped(TypedStdin):
        def isatty(self):
            return False
    monkeypatch.setattr(sys, "stdin", Piped())
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    code, _, raw, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_TERMINAL}\n" and "approval code" not in raw and ctx.ledger.read_bytes() == before


def test_stdout_must_be_a_terminal_too(ctx, capsys, monkeypatch):
    before = stale(ctx)
    monkeypatch.setattr(sys, "stdin", TypedStdin())
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    code, _, raw, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_TERMINAL}\n" and "approval code" not in raw and ctx.ledger.read_bytes() == before


def test_the_ledger_edited_between_the_prompt_and_the_code_is_refused(ctx, capsys, monkeypatch):
    before = stale(ctx)

    def edit(code):
        ctx.ledger.write_bytes(before.replace(b"Last synced:** ", b"Last synced:** x"))
    terminal(monkeypatch, None, before=edit)
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err == f"error: {rules.CONFIRM_SWAPPED}\n" and b"BR-1201-1@aaaaaaaa" in ctx.ledger.read_bytes()


def test_a_rules_file_edited_between_the_prompt_and_the_code_is_refused(ctx, capsys, monkeypatch):
    before = stale(ctx)
    path = ctx.root / "dod" / "Rules_1201.md"

    def edit(code):
        path.write_text(path.read_text(encoding="utf-8").replace("Customers earn 1 points per euro.", "Customers earn 100 points per euro."), encoding="utf-8")
    terminal(monkeypatch, None, before=edit)
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err.startswith("error: ") and "nothing" in err and ctx.ledger.read_bytes() == before


def test_the_ledger_replaced_by_a_symlink_before_the_write_is_refused(ctx, monkeypatch):
    before = stale(ctx)
    prep = rules.prepare_confirm("dod", 1001)
    real = ctx.root / "elsewhere.md"
    real.write_bytes(before)
    ctx.ledger.unlink()
    ctx.ledger.symlink_to(real)
    with pytest.raises(ValueError) as e:
        prep["write"]()
    assert str(e.value) == rules.CONFIRM_SWAPPED and real.read_bytes() == before and ctx.ledger.is_symlink() and leftovers(ctx) == []


def test_a_write_error_has_one_fixed_text(ctx, monkeypatch):
    before = stale(ctx)
    prep = rules.prepare_confirm("dod", 1001)
    monkeypatch.setattr(stack, "_atomic_replace", lambda *a, **k: (_ for _ in ()).throw(ValueError(stack._E_BACK)))
    with pytest.raises(ValueError) as e:
        prep["write"]()
    assert str(e.value) == rules.CONFIRM_WRITE and ctx.ledger.read_bytes() == before


def test_the_text_about_to_be_renamed_is_checked(ctx, monkeypatch):
    stale(ctx)
    prep = rules.prepare_confirm("dod", 1001)
    seen = {}
    orig = stack._atomic_replace

    def spy(path, text, check=None, expect=None):
        seen["check"] = check
        return orig(path, text, check, expect)
    monkeypatch.setattr(stack, "_atomic_replace", spy)
    prep["write"]()
    good = ctx.ledger.read_bytes()
    seen["check"](good)
    for bad in (good.replace(b"BR-1201-1@", b"BR-1201-2@"), good.replace(b"Rules from:** 1201, 1200", b"Rules from:** 1201"),
                good.replace(b"Last synced:** ", b"Last synced:** x")):
        with pytest.raises(ValueError) as e:
            seen["check"](bad)
        assert str(e.value) == rules.CONFIRM_WRITE


@pytest.mark.parametrize("eol", ["\r", "\u2028"])
def test_an_unusual_line_terminator_on_the_line_is_refused(ctx, capsys, eol):
    ctx.put(ctx.text().replace(FROM_LINE, "- **Rules from:** 1201, 1200" + eol + "- **Note:** x\n"))
    before = ctx.ledger.read_bytes()
    code, _, _, err = ctx.run(capsys)
    assert code == 2 and err.startswith("error: ") and ctx.ledger.read_bytes() == before


def test_early_typed_code_is_flushed_and_the_same_code_typed_after_the_prompt_confirms(ctx):
    stale(ctx)
    before = ctx.ledger.read_bytes()
    early = rules.prepare_confirm("dod", 1001)["code"]
    code, screen, err = run_in_pty(CMD, ctx.root, early=early, answer="")  # then end of input at the prompt
    assert code == 2 and stack.E_NOT_CONFIRMED in err and ctx.ledger.read_bytes() == before, (screen, err)
    code, screen, err = run_in_pty(CMD, ctx.root)
    assert code == 0 and ctx.value() == ctx.want(), (screen, err)
    assert "BR-1201-1" in screen and "aaaaaaaa" in screen and "Customers earn" not in screen


def test_a_real_process_without_terminals_refuses_a_re_confirmation(ctx):
    stale(ctx)
    before = ctx.ledger.read_bytes()
    for kw in ({"stdout_pty": False}, {"stdin_pty": False}, {"stdout_pty": False, "stdin_pty": False}):
        code, screen, err = run_in_pty(CMD, ctx.root, **kw)
        assert code == 2 and rules.CONFIRM_TERMINAL in err and "approval code" not in screen and ctx.ledger.read_bytes() == before, kw
    code, screen, err = run_in_pty(CMD, ctx.root, answer="not-the-code")
    assert code == 2 and ctx.ledger.read_bytes() == before


def test_a_real_process_without_terminals_still_adds_new_ids(ctx):
    code, screen, err = run_in_pty(CMD, ctx.root, stdout_pty=False, stdin_pty=False)
    assert code == 0 and ctx.value() == ctx.want() and '"written": true' in screen, (screen, err)


def test_confirm_makes_no_request_and_needs_no_token(ctx, monkeypatch):
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    assert ctx.m.main(CMD) == 0


def test_help_and_parser_for_confirm(capsys):
    from ado.__main__ import main
    with pytest.raises(SystemExit):
        main(["rules", "--help"])
    assert "--confirm" in capsys.readouterr().out
    for argv in (["rules", "--confirm"], ["rules", "1001", "--confirm", "--apply"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2, argv


def test_run_in_pty_kills_a_child_that_waits_forever_within_the_timeout():
    import time
    start = time.monotonic()
    for kw in ({}, {"stdout_pty": False}):
        with pytest.raises(pytest.fail.Exception) as e:
            run_in_pty([], ".", timeout=2, argv=[sys.executable, "-c", "import time; time.sleep(60)"], **kw)
        assert "did not finish within 2 s and was killed" in str(e.value)
    assert time.monotonic() - start < 15


def test_the_new_line_takes_the_terminator_of_the_files_first_line(ctx, capsys, monkeypatch):
    mixed = ctx.text().replace("\n", "\r\n", 1)  # the first line is CRLF, every other line LF
    ctx.put(mixed)
    assert ctx.run(capsys)[0] == 0
    want = f"- **Rules reviewed:** {ctx.want()}"
    assert ctx.ledger.read_bytes() == mixed.replace(FROM_LINE, FROM_LINE + want + "\r\n").encode()
    stale_line = f"- **Rules reviewed:** BR-1200-1@{ctx.h['BR-1200-1']}, BR-1201-1@aaaaaaaa\n"  # an LF line in a file whose first line is CRLF
    ctx.put(ctx.text().replace(want + "\r\n", stale_line))
    before = ctx.text()
    terminal(monkeypatch)
    assert ctx.run(capsys)[0] == 0
    assert ctx.ledger.read_bytes() == before.replace(stale_line, want + "\r\n").encode()  # rewritten with the first line's terminator, the rest as it was
    lf = mixed.replace("\r\n", "\n", 1)  # the reverse: first line LF, the Rules from line CRLF
    ctx.put(lf.replace(FROM_LINE, FROM_LINE.replace("\n", "\r\n")))
    assert ctx.run(capsys)[0] == 0
    assert ctx.ledger.read_bytes() == lf.replace(FROM_LINE, FROM_LINE.replace("\n", "\r\n") + want + "\n").encode()
