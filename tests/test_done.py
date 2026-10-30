import json
from pathlib import Path

import pytest

from ado import dod, done
from ado.config import Config
from tests.conftest import FakeClient

S5 = "## 5. Definition of Done  (all ✅ to close)\n"


def ledger(*items, tail=""):
    return "# DoD\n\n- [ ] not in section 5\n\n" + dod.SECTION2 + "\n\n" + S5 + "".join(f"{i}\n" for i in items) + tail


def test_check_all_ticked():
    assert done.check(ledger("- [x] a", "- [X] b")) == {"ready": True, "unticked": []}


def test_check_lists_unticked():
    r = done.check(ledger("- [x] a", "- [ ] b", "- [ ] c"))
    assert r == {"ready": False, "unticked": ["b", "c"]}


def test_missing_section5():
    assert done.check("# DoD\n- [x] a\n") == {"ready": False, "unticked": ["section 5 missing"]}


def test_section5_without_checklist():
    r = done.check(ledger("just prose"))
    assert r == {"ready": False, "unticked": ["section 5 has no checklist"]}


def test_check_ignores_boxes_after_next_heading():
    r = done.check(ledger("- [x] a", tail="\n## 6. Notes\n- [ ] later\n"))
    assert r["ready"] is True


def test_render_plan_ready_and_blocked(cfg):
    ok = done.render_plan(1001, {"ready": True, "unticked": []}, cfg)
    assert "DoD_1001.md" in ok and "Done" in ok
    bad = done.render_plan(1001, {"ready": False, "unticked": ["a|b\nc\x1b[31m"]}, cfg)
    assert "Not ready" in bad and "a\\|b c [31m" in bad
    assert "\x1b" not in bad


def _ledger_file(tmp_path, *items):
    p = tmp_path / "dod" / "DoD_1001.md"
    p.parent.mkdir()
    p.write_text(ledger(*items), encoding="utf-8")
    return p


def test_apply_attaches_then_transitions_to_configured_state(tmp_path):
    p = _ledger_file(tmp_path, "- [x] a")
    c = FakeClient(Config(organization="contoso", project="Fabrikam", done_state="Closed"),
                   responses=[{"url": "https://x/att/1"}, {}, {"id": 1001}])
    res = done.apply(c, 1001, p)
    assert res["attached"][0]["path"] == str(p) and res["failed"] == []
    assert [x["method"] for x in c.calls] == ["POST", "PATCH", "PATCH"]
    assert c.calls[1]["body"][0]["value"]["attributes"]["comment"] == "DoD complete"
    assert c.calls[2]["body"] == [{"op": "add", "path": "/fields/System.State", "value": "Closed"}]
    assert res["state"] == "Closed"


def test_apply_does_not_transition_when_attach_fails(tmp_path, cfg):
    p = _ledger_file(tmp_path, "- [x] a")
    c = FakeClient(cfg)
    c.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    res = done.apply(c, 1001, p)
    assert res["state"] is None and res["failed"] and c.calls == []


# ---- CLI ----

def _env(tmp_path, monkeypatch, responses=()):
    from ado import __main__ as m
    (tmp_path / "ado.config.json").write_text(
        json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    fake = {}

    def make(cfg):
        fake["c"] = FakeClient(cfg, responses=list(responses))
        return fake["c"]
    monkeypatch.setattr(m, "AdoClient", make)
    return m, fake


def _write(tmp_path, *items):
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_1001.md").write_text(ledger(*items), encoding="utf-8")


def test_dry_run_no_writes(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a")
    m, fake = _env(tmp_path, monkeypatch)
    assert m.main(["done", "1001", "--plan-out", "out/plan.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ready"] is True and out["applied"] is False
    assert [c["method"] for c in fake["c"].calls] == ["GET"]  # read-only
    assert "DoD_1001.md" in (tmp_path / "out" / "plan.md").read_text()


def test_apply_refused_when_not_ready(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a", "- [ ] b")
    m, fake = _env(tmp_path, monkeypatch)
    assert m.main(["done", "1001", "--apply"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ready"] is False and out["unticked"] == ["b"]
    assert fake["c"].calls == []


def test_cli_missing_ledger_exits_2_no_calls(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch)
    assert m.main(["done", "1001", "--apply"]) == 2
    assert "DoD_1001.md" in capsys.readouterr().err
    assert fake["c"].calls == []


def test_cli_apply_success(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a")
    m, fake = _env(tmp_path, monkeypatch, [{}, {"url": "u"}, {}, {}])
    assert m.main(["done", "1001", "--apply"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "Done" and out["applied"] is True
    assert [c["method"] for c in fake["c"].calls] == ["GET", "POST", "PATCH", "PATCH"]


def test_cli_apply_attach_failure_exits_1_no_transition(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a")
    m, fake = _env(tmp_path, monkeypatch)
    monkeypatch.setattr(m.done.attachments, "attach",
                        lambda *a, **k: {"attached": [], "failed": [{"path": "p", "error": "e"}]})
    assert m.main(["done", "1001", "--apply"]) == 1
    assert json.loads(capsys.readouterr().out)["state"] is None
    assert [c["method"] for c in fake["c"].calls] == ["GET"]


# ---- fix round 1 ----

@pytest.mark.parametrize("line", ["* [ ] b", "+ [ ] b", "  - [ ] b", "-  [ ] b", "- [ ]", "\t- [ ] b"])
def test_check_box_variants_fail_closed(line):
    r = done.check(ledger("- [x] a", line))
    assert r["ready"] is False and len(r["unticked"]) == 1


@pytest.mark.parametrize("line", ["* [x] b", "  - [X] b", "-  [x] b"])
def test_check_ticked_variants_ready(line):
    assert done.check(ledger("- [x] a", line))["ready"] is True


def test_apply_keeps_attach_result_when_transition_fails(tmp_path, cfg):
    from ado.client import AdoError
    p = _ledger_file(tmp_path, "- [x] a")
    c = FakeClient(cfg, responses=[{"url": "https://x/att/1"}, {}])
    orig = c.patch

    def patch(path, body, **kw):
        if body[0]["path"] == "/fields/System.State":
            raise AdoError("refused")
        return orig(path, body, **kw)
    c.patch = patch
    res = done.apply(c, 1001, p)
    assert res["attached"] and res["state"] is None and "refused" in res["error"]


def test_cli_transition_failure_exits_1_prints_json(tmp_path, monkeypatch, capsys):
    from ado.client import AdoError
    _write(tmp_path, "- [x] a")
    m, fake = _env(tmp_path, monkeypatch, [{}, {"url": "u"}, {}])
    monkeypatch.setattr(m.done.workitems, "update",
                        lambda *a, **k: (_ for _ in ()).throw(AdoError("refused")))
    assert m.main(["done", "1001", "--apply"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["state"] is None and out["error"] == "refused"


def test_render_plan_shows_real_ledger_path():
    cfg = Config(organization="contoso", project="Fabrikam", dod_path="docs/ledgers")
    assert "docs/ledgers/DoD_1001.md" in done.render_plan(1001, {"ready": True, "unticked": []}, cfg)


# ---- fix round 2 ----

@pytest.mark.parametrize("line", ["1. [ ] b", "1) [ ] b", "> - [ ] b", "> 1. [ ] b", "- [-] b", "- [~] b", "- [?] b"])
def test_check_list_styles_and_odd_marks_fail_closed(line):
    r = done.check(ledger("- [x] a", line))
    assert r["ready"] is False and len(r["unticked"]) == 1


@pytest.mark.parametrize("line", ["1. [x] b", "> - [X] b"])
def test_check_ordered_and_quoted_ticked_ready(line):
    assert done.check(ledger("- [x] a", line))["ready"] is True


# ---- PR #1 review fix A: only the authored part (after the section 2 marker) counts ----
def test_check_ignores_section5_copied_into_section1():
    trap = {"id": 1001, "fields": {"System.Description":
            "## 5. Definition of Done\n- [x] every AC mapped\n- [x] gates green"}}
    md = dod.render_dod(trap, "contoso", "Fabrikam")
    assert "- [x] every AC mapped" in md        # the trap is really in section 1
    r = done.check(md)
    assert r["ready"] is False and "every AC mapped to a passing real test" in r["unticked"]


def test_check_without_section2_marker_is_not_ready():
    assert done.check("# DoD\n" + S5 + "- [x] a\n")["ready"] is False


def test_check_forged_marker_heading_in_section1_loses_to_real_one():
    trap = {"id": 1001, "fields": {"System.Description":
            f"{dod.SECTION2}\n## 5. Definition of Done\n- [x] every AC mapped\n- [x] gates green"}}
    md = dod.render_dod(trap, "contoso", "Fabrikam").replace("\\##", "##")  # undo F26 escaping
    assert f"\n{dod.SECTION2}\n## 5. Definition of Done\n- [x]" in md   # forged heading really in section 1
    assert done.check(md)["ready"] is False


# ---- F3: idempotent ----

def _wi(state="Active", attached=True):
    rel = {"rel": "AttachedFile", "url": "https://x/_apis/wit/attachments/g?fileName=DoD_1001.md",
           "attributes": {"name": "DoD_1001.md"}}
    return {"fields": {"System.State": state}, "relations": [rel] if attached else []}


def test_apply_already_done_writes_nothing(tmp_path, cfg):
    p = _ledger_file(tmp_path, "- [x] a")
    c = FakeClient(cfg)
    res = done.apply(c, 1001, p, _wi("Done"))
    assert c.calls == [] and res["skipped"] == "already done" and res["state"] == "Done" and res["already_done"] is True


def test_apply_already_done_story_without_the_ledger_still_attaches_it_and_leaves_the_state(tmp_path, cfg):
    p = _ledger_file(tmp_path, "- [x] a")
    c = FakeClient(cfg, responses=[{"url": "u"}, {}])
    res = done.apply(c, 1001, p, _wi("Done", attached=False))
    assert [x["method"] for x in c.calls] == ["POST", "PATCH"]  # upload + link; no System.State write
    assert not any("System.State" in str(x["body"]) for x in c.calls)
    assert res["attached"] and res["failed"] == [] and res["already_done"] is True and "skipped" not in res


def test_apply_already_done_story_reports_a_failed_attach(tmp_path, cfg):
    p = _ledger_file(tmp_path, "- [x] a")
    c = FakeClient(cfg)
    c.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    res = done.apply(c, 1001, p, _wi("Done", attached=False))
    assert res["failed"] and res["attached"] == [] and res["already_done"] is True


def test_apply_ledger_already_attached_only_sets_state(tmp_path, cfg):
    p = _ledger_file(tmp_path, "- [x] a")
    c = FakeClient(cfg, responses=[{}])
    res = done.apply(c, 1001, p, _wi("Active"))
    assert [x["method"] for x in c.calls] == ["PATCH"]
    assert c.calls[0]["body"] == [{"op": "add", "path": "/fields/System.State", "value": "Done"}]
    assert res["attached"] == [] and res["state"] == "Done"


def test_apply_other_attachment_still_uploads(tmp_path, cfg):
    p = _ledger_file(tmp_path, "- [x] a")
    wi = _wi("Active")
    wi["relations"][0]["attributes"]["name"] = "other.md"
    wi["relations"][0]["url"] = "https://x/_apis/wit/attachments/g?fileName=other.md"
    c = FakeClient(cfg, responses=[{"url": "u"}, {}, {}])
    done.apply(c, 1001, p, wi)
    assert [x["method"] for x in c.calls] == ["POST", "PATCH", "PATCH"]


def test_render_plan_states_idempotent_outcomes(cfg):
    ready = {"ready": True, "unticked": []}
    assert "already in done; nothing to do" in done.render_plan(1001, ready, cfg, _wi("Done")).lower()
    p = done.render_plan(1001, ready, cfg, _wi("Active"))
    assert "ledger already attached; will only set state" in p.lower() and "1. attach" not in p
    assert "1. attach" in done.render_plan(1001, ready, cfg, _wi("Active", attached=False))
    late = done.render_plan(1001, ready, cfg, _wi("Done", attached=False))  # closed by hand, ledger never attached
    assert "already in done" in late.lower() and "1. attach" in late and "set state" not in late
    assert "nothing to do" not in late.lower()


def test_cli_apply_twice_second_run_is_noop(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a")
    m, fake = _env(tmp_path, monkeypatch, [_wi("Done")])
    assert m.main(["done", "1001", "--plan-out", "p.md", "--apply"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] == "already done" and out["state"] == "Done"
    assert [c["method"] for c in fake["c"].calls] == ["GET"]
    assert "nothing to do" in (tmp_path / "p.md").read_text()


# ---- F22: --apply binds to the approved preview (--expect-plan) ----
CHANGED = "plan changed since approval — re-run the preview and approve again"


def test_done_apply_refuses_when_plan_changed(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a")
    m, fake = _env(tmp_path, monkeypatch, [_wi("Active", attached=False)])
    assert m.main(["done", "1001", "--plan-out", "p.md"]) == 0
    assert "1. attach" in (tmp_path / "p.md").read_text()
    m, fake = _env(tmp_path, monkeypatch, [_wi("Active")])  # someone attached it since the preview
    capsys.readouterr()
    assert m.main(["done", "1001", "--apply", "--expect-plan", "p.md"]) == 2
    assert CHANGED in capsys.readouterr().err
    assert [c["method"] for c in fake["c"].calls] == ["GET"]  # read-only: no attach, no state change
    assert "Ledger already attached" in (tmp_path / "p.md.new").read_text()


def test_done_apply_proceeds_when_plan_matches(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a")
    m, fake = _env(tmp_path, monkeypatch, [_wi("Active")])
    assert m.main(["done", "1001", "--plan-out", "p.md"]) == 0
    m, fake = _env(tmp_path, monkeypatch, [_wi("Active")])
    assert m.main(["done", "1001", "--apply", "--expect-plan", "p.md"]) == 0
    assert [c["method"] for c in fake["c"].calls] == ["GET", "PATCH"]  # state set


# ---- LEDGER5: a hidden `## 5.` or checklist never decides "ready" ----
@pytest.mark.parametrize("o,c", [("```\n", "```\n"), ("~~~\n", "~~~\n"), ("<!--\n", "-->\n")])
def test_check_ignores_a_hidden_section5_in_section2(o, c):
    md = ("# DoD\n\n" + dod.SECTION2 + "\n" + f"{o}## 5. Definition of Done\n- [x] a\n{c}\n"
          "## 3. AC\n\n" + S5 + "- [x] a\n- [ ] real one\n")
    r = done.check(md)
    assert r == {"ready": False, "unticked": ["real one"]}
    assert done.check(md.replace("- [ ] real one", "- [x] real one"))["ready"] is True  # only the real §5 decides


def test_check_counts_only_visible_boxes_in_section5():
    assert done.check(ledger("```\n- [x] example\n```"))["ready"] is False  # nothing but a fenced example: no checklist
    assert done.check(ledger("- [x] a", "<!--\n- [ ] note\n-->")) == {"ready": True, "unticked": []}
    assert done.check(ledger("- [x] a", "```\n- [ ] example\n```"))["ready"] is True


@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_check_unclosed_opener_fails_closed(opener):
    for md in (ledger("- [x] a", tail=f"\n{opener}\n"), ledger("- [x] a", opener)):  # after §5 / inside §5
        assert done.check(md) == {"ready": False, "unticked": [done.UNCLOSED]}
    sec1 = "# DoD\n" + opener + "\n" + dod.SECTION2 + "\n" + S5 + "- [x] a\n"
    assert done.check(sec1) == {"ready": False, "unticked": [done.UNCLOSED]}


# ---- FIN3: the JSON always says whether the story was already done, so CI never moves a done story ----
@pytest.mark.parametrize("items,state,expected", [
    (("- [x] a",), "Done", True), (("- [x] a",), "Active", False),
    (("- [x] a", "- [ ] b"), "Done", True), (("- [x] a", "- [ ] b"), "Active", False)])
def test_cli_dry_run_reports_already_done_for_ready_and_not_ready_ledgers(tmp_path, monkeypatch, capsys, items, state, expected):
    _write(tmp_path, *items)
    m, fake = _env(tmp_path, monkeypatch, [_wi(state)])
    m.main(["done", "1001"])
    assert json.loads(capsys.readouterr().out)["already_done"] is expected
    assert [c["method"] for c in fake["c"].calls] == ["GET"]


def test_cli_failed_attach_on_a_done_story_exits_1_but_still_prints_already_done(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a")
    m, fake = _env(tmp_path, monkeypatch, [_wi("Done", attached=False)])
    monkeypatch.setattr(m.done.attachments, "attach",
                        lambda *a, **k: {"attached": [], "failed": [{"path": "p", "error": "e"}]})
    assert m.main(["done", "1001", "--apply"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["already_done"] is True and out["failed"] and out["state"] == "Done"
    assert [c["method"] for c in fake["c"].calls] == ["GET"]


def test_cli_unreachable_story_on_a_dry_run_just_omits_already_done(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a", "- [ ] b")
    m, _ = _env(tmp_path, monkeypatch)
    monkeypatch.setattr(m.workitems, "get", lambda *a, **k: (_ for _ in ()).throw(m.AdoError("nope")))
    assert m.main(["done", "1001"]) == 1
    assert "already_done" not in json.loads(capsys.readouterr().out)


# ---- V3-5b: Decisions_<id>.md is attached next to the ledger when it exists ----
def _rel(name):
    return {"rel": "AttachedFile", "url": f"https://x/_apis/wit/attachments/g?fileName={name}", "attributes": {"name": name}}


def _wi_with(state, *names):
    return {"fields": {"System.State": state}, "relations": [_rel(n) for n in names]}


def _decisions_file(tmp_path):
    p = tmp_path / "dod" / "Decisions_1001.md"
    p.parent.mkdir(exist_ok=True)
    p.write_text("# Decisions — US1001\n", encoding="utf-8")
    return p


def test_done_attaches_decisions_file_once_and_plan_lists_it(tmp_path, monkeypatch, capsys, cfg):
    ready = {"ready": True, "unticked": []}
    led, dec = _ledger_file(tmp_path, "- [x] a"), _decisions_file(tmp_path)
    # plan: both files, ledger first, then the state
    p = done.render_plan(1001, ready, cfg, _wi_with("Active"), decisions=dec)
    led_cell = str(Path("dod") / "DoD_1001.md")  # the plan names the ledger from config.dod_path
    assert f"1. attach {led_cell}" in p and f"2. attach {dec}" in p and "3. set state to Done" in p
    # the ledger is attached already: only the decisions file and the state remain
    p = done.render_plan(1001, ready, cfg, _wi_with("Active", "DoD_1001.md"), decisions=dec)
    assert f"1. attach {dec}" in p and led_cell not in p and "2. set state to Done" in p
    # both attached: only the state
    p = done.render_plan(1001, ready, cfg, _wi_with("Active", "DoD_1001.md", "Decisions_1001.md"), decisions=dec)
    assert "1. attach" not in p and "will only set state to Done" in p
    # closed by hand with the decisions file missing: attach it, no state change
    p = done.render_plan(1001, ready, cfg, _wi_with("Done", "DoD_1001.md"), decisions=dec)
    assert f"1. attach {dec}" in p and "set state" not in p and "nothing to do" not in p.lower()
    assert "nothing to do" in done.render_plan(1001, ready, cfg, _wi_with("Done", "DoD_1001.md", "Decisions_1001.md"),
                                               decisions=dec).lower()
    # apply: both uploaded in order, then the state
    c = FakeClient(cfg, responses=[{"url": "u1"}, {}, {"url": "u2"}, {}, {}])
    res = done.apply(c, 1001, led, _wi_with("Active"), decisions=dec)
    assert [x["method"] for x in c.calls] == ["POST", "PATCH", "POST", "PATCH", "PATCH"]
    assert [a["path"] for a in res["attached"]] == [str(led), str(dec)] and res["state"] == "Done"
    # idempotent: attached by name -> skipped; a second apply writes nothing
    c = FakeClient(cfg)
    res = done.apply(c, 1001, led, _wi_with("Done", "DoD_1001.md", "Decisions_1001.md"), decisions=dec)
    assert c.calls == [] and res["skipped"] == "already done"
    c = FakeClient(cfg, responses=[{"url": "u"}, {}, {}])
    done.apply(c, 1001, led, _wi_with("Active", "DoD_1001.md"), decisions=dec)  # only the decisions file uploaded
    assert [x["method"] for x in c.calls] == ["POST", "PATCH", "PATCH"] and "Decisions_1001.md" in c.calls[0]["path"]


def test_done_decisions_file_uses_the_attach_safety_rules(tmp_path, cfg):
    led, dec = _ledger_file(tmp_path, "- [x] a"), _decisions_file(tmp_path)
    cfg.attachments_allow = [".txt"]  # neither is allowed: refused, and the story is not moved
    c = FakeClient(cfg)
    res = done.apply(c, 1001, led, _wi_with("Active"), decisions=dec)
    assert [f["path"] for f in res["failed"]] == [str(led), str(dec)] and res["state"] is None and c.calls == []


def test_done_cli_decisions_file_attached_and_reported(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a")
    dec = _decisions_file(tmp_path)
    m, fake = _env(tmp_path, monkeypatch, [_wi_with("Active", "DoD_1001.md")])
    assert m.main(["done", "1001", "--plan-out", "p.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decisions_file"] == str(Path("dod") / "Decisions_1001.md")
    assert f"1. attach {Path('dod') / 'Decisions_1001.md'}" in (tmp_path / "p.md").read_text()
    assert [c["method"] for c in fake["c"].calls] == ["GET"]
    m, fake = _env(tmp_path, monkeypatch, [_wi_with("Active", "DoD_1001.md"), {"url": "u"}, {}, {}])
    assert m.main(["done", "1001", "--apply", "--expect-plan", "p.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [a["path"] for a in out["attached"]] == [str(Path("dod") / "Decisions_1001.md")] and out["state"] == "Done"
    assert dec.exists()


def test_done_without_file_unchanged(tmp_path, monkeypatch, capsys, cfg):
    ready = {"ready": True, "unticked": []}
    for state, names, text in (("Active", (), "1. attach"), ("Active", ("DoD_1001.md",), "ledger already attached"),
                               ("Done", (), "ledger not attached yet"), ("Done", ("DoD_1001.md",), "nothing to do")):
        assert done.render_plan(1001, ready, cfg, _wi_with(state, *names)) == done.render_plan(
            1001, ready, cfg, _wi_with(state, *names), decisions=None)
        assert text in done.render_plan(1001, ready, cfg, _wi_with(state, *names)).lower()
    _write(tmp_path, "- [x] a")
    m, fake = _env(tmp_path, monkeypatch, [_wi_with("Active"), {"url": "u"}, {}, {}])  # each main() builds a fresh client with this queue
    assert m.main(["done", "1001", "--plan-out", "p.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "decisions_file" not in out and "Decisions_" not in (tmp_path / "p.md").read_text()
    assert m.main(["done", "1001", "--apply"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "decisions_file" not in out and [a["path"] for a in out["attached"]] == [str(Path("dod") / "DoD_1001.md")]
    assert not any("Decisions_" in str(c) for c in fake["c"].calls)


# ---- V3-5b fix round 1: a symlinked ledger or decisions file is never read or uploaded ----
def _outside_secret(tmp_path):
    out = tmp_path.parent / (tmp_path.name + "-outside")
    out.mkdir()
    (out / "secret.md").write_text("TOPSECRET\n")
    return out / "secret.md"


@pytest.mark.parametrize("target", ["outside", "inside"])
def test_done_never_uploads_a_symlinked_decisions_file(tmp_path, monkeypatch, capsys, target):
    _write(tmp_path, "- [x] a")
    dec = tmp_path / "dod" / "Decisions_1001.md"
    real = _outside_secret(tmp_path) if target == "outside" else tmp_path / "dod" / "real.md"
    if target == "inside":
        real.write_text("# Decisions\n")
    dec.symlink_to(real)
    m, fake = _env(tmp_path, monkeypatch, [_wi_with("Active", "DoD_1001.md"), {"url": "u"}, {}, {}])
    for argv in (["done", "1001", "--plan-out", "p.md"], ["done", "1001", "--apply"]):
        assert m.main(argv) == 2
        err = capsys.readouterr().err
        assert "decisions file" in err and "TOPSECRET" not in err and "secret.md" not in err
        assert fake["c"].calls == []  # not even the story read
    assert not (tmp_path / "p.md").exists()


def test_done_never_uploads_a_symlinked_ledger(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "- [x] a")
    led = tmp_path / "dod" / "DoD_1001.md"
    real = tmp_path / "dod" / "real_ledger.md"
    real.write_text(led.read_text())  # a perfectly ready ledger, reached through a link
    led.unlink()
    led.symlink_to(real)
    m, fake = _env(tmp_path, monkeypatch, [{}, {"url": "u"}, {}, {}])
    assert m.main(["done", "1001", "--apply"]) == 2
    assert "ledger" in capsys.readouterr().err and fake["c"].calls == []
    led.unlink()
    led.symlink_to(_outside_secret(tmp_path))
    assert m.main(["done", "1001", "--apply"]) == 2 and fake["c"].calls == []


def test_done_refuses_a_decisions_file_that_is_not_utf8(tmp_path, monkeypatch):
    _write(tmp_path, "- [x] a")
    dec = tmp_path / "dod" / "Decisions_1001.md"
    dec.write_bytes(b"\xff\xfe")
    m, fake = _env(tmp_path, monkeypatch, [_wi_with("Active", "DoD_1001.md"), {}])
    assert m.main(["done", "1001", "--apply"]) == 2 and fake["c"].calls == []
