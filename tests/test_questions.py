import json

import pytest

from ado.__main__ import dispatch, main
from ado.config import Config
from ado.dod import SECTION2, render_dod
from ado.gate import render_summary, run_gate
from ado import questions as qmod
from ado.client import AdoError
from ado.questions import parse_questions
from tests.conftest import FakeClient

QS = """- [ ] Q1: Are contractors included? → _(open)_ · owner: PO
- [x] Q2: Which locale? → en-GB only (PO comment 2026-07-01) · owner: PO
-   [ ]  Q3 :  Retention period ->   _(open)_
- [ ] Q4: Export format? → CSV · owner: Dev
- [x] Q5: Cap? → _(open)_ · owner: PO
"""


def _ledger(body, tail="## 3. AC\n"):
    return f"# DoD\n{SECTION2}\n- **Approach:** x\n{body}{tail}"


def test_parse_open_answered_lenient():
    r = parse_questions(_ledger(QS))
    assert r["open"] == [
        {"id": "Q1", "question": "Are contractors included?", "owner": "PO"},
        {"id": "Q3", "question": "Retention period", "owner": "?"},
        {"id": "Q4", "question": "Export format?", "owner": "Dev"},  # box [ ] with an answer is still open
        {"id": "Q5", "question": "Cap?", "owner": "PO"},            # [x] with _(open)_ is still open
    ]
    assert r["answered"] == [{"id": "Q2", "question": "Which locale?", "owner": "PO",
                              "answer": "en-GB only (PO comment 2026-07-01)"}]


def test_parse_ignores_fences_comments_and_other_sections():
    body = ("```\n- [ ] Q1: fenced → _(open)_ · owner: PO\n```\n<!-- - [ ] Q2: hidden → _(open)_ -->\n"
            "<!--\n- [ ] Q3: multi → _(open)_\n-->\n- [ ] Q4: real → _(open)_ · owner: PO\n")
    r = parse_questions(_ledger(body, "## 3. AC\n- [ ] Q9: elsewhere → _(open)_\n"))
    assert [q["id"] for q in r["open"]] == ["Q4"] and r["answered"] == []


def test_parse_no_section2():
    assert parse_questions("nothing") == {"open": [], "answered": [], "unparsed": False}


def test_dod_seed_has_format_hint_not_a_question():
    md = render_dod({"id": 1001, "fields": {}}, "contoso", "Fabrikam", synced="2026-07-14")
    assert "Format, one line each: `- [ ] Q1:" in md
    assert parse_questions(md) == {"open": [], "answered": [], "unparsed": False}
    assert "_(fill in as they arise)_" in md


def test_questions_cli_json(tmp_path, monkeypatch, capsys):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_1001.md").write_text(_ledger(QS), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    assert main(["questions", "1001"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [q["id"] for q in out["open"]] == ["Q1", "Q3", "Q4", "Q5"] and out["answered"][0]["id"] == "Q2"


def test_questions_missing_ledger_is_error(tmp_path, capsys):
    cfg = Config("contoso", "Fabrikam", dod_path=str(tmp_path))
    args = type("A", (), {"command": "questions", "id": 7, "post": False, "plan_out": None})()
    client = FakeClient(cfg)
    with pytest.raises(ValueError, match="run `ado dod 7` first"):
        dispatch(client, args)
    assert client.calls == []


def test_gate_open_questions_advisory_then_blocking_and_summary():
    led = _ledger(QS)
    r = run_gate(led, {}, {})
    assert r["open_questions"] == ["Q1", "Q3", "Q4", "Q5"] and not r["blocking"] and r["blocked_by"] == []
    assert "**Advisory" in render_summary(r) and "`Q1`, `Q3`, `Q4`, `Q5`" in render_summary(r)
    b = run_gate(led, {}, {}, block=["open_questions"])
    assert b["blocking"] and b["blocked_by"] == ["open_questions"]
    blocking = render_summary(b).partition("**Advisory")[0].partition("**Blocking")[2]
    assert "Open questions" in blocking and "`Q1`" in blocking


def test_gate_no_open_questions_never_blocks():
    r = run_gate(_ledger("- [x] Q1: a? → b · owner: PO\n"), {}, {}, block=["open_questions"])
    assert r["open_questions"] == [] and not r["blocking"]


def test_gate_questions_closed_as_assumed_are_not_open_but_an_open_one_still_is():
    closed = _ledger("- [x] Q1: a? → assumed: x is used · owner: PO\n- [x] Q2: b? → assumed: y · owner: PO\n")
    r = run_gate(closed, {}, {}, block=["open_questions"])
    assert r["open_questions"] == [] and not r["blocking"] and r["blocked_by"] == []
    mixed = run_gate(_ledger("- [x] Q1: a? → assumed: x · owner: PO\n- [ ] Q2: b? → _(open)_ · owner: PO\n"),
                     {}, {}, block=["open_questions"])
    assert mixed["open_questions"] == ["Q2"] and mixed["blocking"] and mixed["blocked_by"] == ["open_questions"]


def test_empty_assumed_answer_stays_open():
    led = _ledger("- [x] Q1: a? → assumed: · owner: PO\n- [x] Q2: b? → assumed:   \n- [x] Q3: c? → assumed: y · owner: PO\n")
    out = parse_questions(led)
    assert [q["id"] for q in out["open"]] == ["Q1", "Q2"] and [q["id"] for q in out["answered"]] == ["Q3"]


def test_config_accepts_open_questions_in_gate_block(tmp_path):
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "gate": {"block": ["open_questions"]}}))
    assert Config.load(tmp_path).gate["block"] == ["open_questions"]


@pytest.mark.parametrize("f", ["README.md", "AGENTS.md", "rules/hitl.md", "commands/status.md",
                               "commands/plan.md", "commands/pr.md", "docs/configuration.md"])
def test_docs_cover_open_questions(f):
    from pathlib import Path
    text = (Path(__file__).parent.parent / f).read_text(encoding="utf-8")
    assert "ado questions" in text or "open_questions" in text


def test_unclosed_fence_or_comment_in_section2_is_unreadable_and_blocks():
    from ado.gate import UNPARSED_NOTE
    for opener in ("```\n", "<!--\n"):
        led = _ledger(opener + "- [ ] Q1: hidden → _(open)_ · owner: PO\n")
        assert parse_questions(led)["unparsed"] is True
        r = run_gate(led, {}, {}, block=["open_questions"])
        assert r["open_questions"] == ["?"] and r["open_questions_note"] == UNPARSED_NOTE
        assert r["blocking"] and r["blocked_by"] == ["open_questions"]
        assert "could not be read" in render_summary(r)
    assert parse_questions(_ledger(QS))["unparsed"] is False


def test_trailing_open_answer_wins_over_box_and_arrow_in_question():
    r = parse_questions(_ledger("- [x] Q1: Does A -> B hold? → _(open)_ · owner: PO\n"))
    assert r["answered"] == [] and r["open"] == [{"id": "Q1", "question": "Does A -> B hold?", "owner": "PO"}]


def test_gate_open_questions_deduped_in_order():
    led = _ledger("- [ ] Q2: a → _(open)_\n- [ ] Q1: b → _(open)_\n- [ ] Q2: c → _(open)_\n")
    assert run_gate(led, {}, {})["open_questions"] == ["Q2", "Q1"]


def test_gate_absent_section2_with_block_does_not_block():
    r = run_gate("# DoD\n## 3. AC\n", {}, {}, block=["open_questions"])
    assert r["open_questions"] == [] and not r["blocking"]


def test_arrow_split_rule_last_unicode_else_first_ascii():
    r = parse_questions(_ledger("- [x] Q1: Retention? → 30 days -> confirmed by PO · owner: PO\n"
                                "- [ ] Q2: Cap -> per user? -> _(open)_ · owner: PO\n"))
    assert r["answered"] == [{"id": "Q1", "question": "Retention?", "owner": "PO",
                              "answer": "30 days -> confirmed by PO"}]
    assert r["open"] == [{"id": "Q2", "question": "Cap", "owner": "PO"}]  # first ASCII arrow, answer `per user? -> _(open)_`


def test_questions_json_dedupes_and_flags_unreadable_section():
    r = parse_questions(_ledger("- [ ] Q2: a → _(open)_\n- [ ] Q1: b → _(open)_\n- [ ] Q2: c → _(open)_\n"))
    assert [q["id"] for q in r["open"]] == ["Q2", "Q1"] and r["open"][0]["question"] == "a"
    u = parse_questions(_ledger("```\n- [ ] Q1: x → _(open)_\n"))
    assert u["open"][-1]["id"] == "?" and "could not be read" in u["open"][-1]["question"]


# ---- N3b: post the open questions as one marked comment ----
M = qmod.MARKER
OPEN2 = "- [ ] Q1: Are contractors <in> scope? → _(open)_ · owner: Dana Fictional\n- [ ] Q3: Retention? → _(open)_\n"
BODY = f"{M}<br>Open questions for the product owner:<br>Q1: Are contractors &lt;in&gt; scope?<br>Q3: Retention?"


ME, OTHER = "00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000b"


def _cm(cid, text, day, by=ME):
    return {"id": cid, "text": text, "createdDate": f"2026-10-{day:02d}T00:00:00Z", "createdBy": {"id": by}}


def _resp(comment_list):
    return [{"authenticatedUser": {"id": ME}}, {"comments": comment_list}]


def _env(tmp_path, monkeypatch, comment_list, body=OPEN2):
    from ado import __main__ as m
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_1001.md").write_text(_ledger(body), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    fake = {"list": list(comment_list)}  # each main() builds a new client; tests change fake["list"] between runs

    def make(cfg):
        fake["c"] = FakeClient(cfg, responses=_resp(fake["list"]))
        return fake["c"]
    monkeypatch.setattr(m, "AdoClient", make)
    return m, fake


def _writes(fake):
    return [c for c in fake["c"].calls if c["method"] != "GET"]


def test_comment_html_escapes_and_never_names_owners():
    parsed = parse_questions(_ledger(OPEN2))
    assert qmod.comment_html(parsed["open"]) == BODY
    assert "Dana" not in BODY and qmod.comment_html([]) == ""
    assert qmod.comment_html([{"id": "?", "question": "unparsed", "owner": "?"}]) == ""


def test_plan_none_when_no_open_questions_and_no_own_comment(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, [], body="- [x] Q1: a → b · owner: PO\n")
    assert m.main(["questions", "1001", "--post", "--plan-out", "p.md"]) == 0
    assert "no open questions; nothing to post" in (tmp_path / "p.md").read_text()
    assert _writes(fake) == []


def test_plan_add_shows_exact_text_and_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, [_cm(5, "unmarked <div>chat</div>", 1)])
    assert m.main(["questions", "1001", "--post"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "add" and _writes(fake) == []
    assert m.main(["questions", "1001", "--plan-out", "p.md"]) == 0  # --plan-out alone is a preview too
    plan = (tmp_path / "p.md").read_text()
    assert "Action: ADD a new comment" in plan
    assert f"> {M}\n> Open questions for the product owner:\n> Q1: Are contractors <in> scope?\n> Q3: Retention?" in plan
    assert "Dana" not in plan and _writes(fake) == []


def test_apply_add_posts_one_comment(tmp_path, monkeypatch):
    m, fake = _env(tmp_path, monkeypatch, [_cm(5, "unmarked", 1)])
    assert m.main(["questions", "1001", "--post", "--apply"]) == 0
    assert _writes(fake) == [{"method": "POST", "body": {"text": BODY}, "content_type": "application/json",
                              "path": "/Fabrikam/_apis/wit/workItems/1001/comments?api-version=7.1-preview.4"}]


def test_marker_picks_newest_marked_comment_and_edits_it(tmp_path, monkeypatch):
    old = f"{M}<br>Open questions for the product owner:<br>Q1: stale"
    m, fake = _env(tmp_path, monkeypatch, [
        _cm(7, old, 1), _cm(9, old + "<br>Q2: stale", 3), _cm(11, "Re: " + M + " unmarked reply", 4)])
    assert m.main(["questions", "1001", "--post", "--plan-out", "p.md"]) == 0
    assert "EDIT the existing questions comment (id 9)" in (tmp_path / "p.md").read_text()
    assert m.main(["questions", "1001", "--post", "--apply", "--expect-plan", "p.md"]) == 0
    assert _writes(fake) == [{"method": "PATCH", "body": {"text": BODY}, "content_type": "application/json",
                              "path": "/Fabrikam/_apis/wit/workItems/1001/comments/9?api-version=7.1-preview.4"}]


def test_unchanged_comment_is_a_noop_even_after_ado_reformats_it(tmp_path, monkeypatch):
    stored = "<div>" + BODY.replace("<br>", "<br />") + "</div>"
    m, fake = _env(tmp_path, monkeypatch, [_cm(9, stored, 3)])
    assert m.main(["questions", "1001", "--post", "--plan-out", "p.md"]) == 0
    assert "unchanged; nothing to post" in (tmp_path / "p.md").read_text()
    fake["list"][:] = [_cm(9, stored, 3)]
    assert m.main(["questions", "1001", "--post", "--apply", "--expect-plan", "p.md"]) == 0
    assert _writes(fake) == []


def test_expect_plan_mismatch_exits_2_without_a_write(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, [])
    assert m.main(["questions", "1001", "--post", "--plan-out", "p.md"]) == 0
    approved = (tmp_path / "p.md").read_text()
    (tmp_path / "dod" / "DoD_1001.md").write_text(_ledger(OPEN2 + "- [ ] Q4: Added after approval? → _(open)_\n"))
    fake["list"][:] = []
    assert m.main(["questions", "1001", "--post", "--apply", "--expect-plan", "p.md"]) == 2
    assert "plan changed since approval" in capsys.readouterr().err
    assert _writes(fake) == [] and (tmp_path / "p.md").read_text() == approved
    assert "Q4" in (tmp_path / "p.md.new").read_text()


def test_foreign_marked_comment_is_ignored_and_own_one_is_edited(tmp_path, monkeypatch):
    theirs = f"{M}<br>Open questions for the product owner:<br>Q1: not ours"
    m, fake = _env(tmp_path, monkeypatch, [_cm(3, theirs, 5, by=OTHER)])
    assert m.main(["questions", "1001", "--post", "--plan-out", "p.md"]) == 0
    assert "Action: ADD a new comment" in (tmp_path / "p.md").read_text()
    fake["c"]._responses[:] = _resp([_cm(3, theirs, 5, by=OTHER), _cm(4, "old own", 2)])  # own one is unmarked
    assert m.main(["questions", "1001", "--post", "--plan-out", "p.md"]) == 0
    assert "Action: ADD" in (tmp_path / "p.md").read_text()
    fake["list"][:] = [_cm(3, theirs, 5, by=OTHER), _cm(4, f"{M}<br>Q1: mine & old", 2)]
    assert m.main(["questions", "1001", "--post", "--plan-out", "p.md"]) == 0
    plan = (tmp_path / "p.md").read_text()
    assert "EDIT the existing questions comment (id 4)" in plan and "Replaces:" in plan and "> Q1: mine & old" in plan
    assert "not ours" not in plan


@pytest.mark.parametrize("who", [{}, {"authenticatedUser": {}}, AdoError("403")])
def test_unresolvable_identity_refuses_without_a_write(tmp_path, monkeypatch, capsys, who):
    m, fake = _env(tmp_path, monkeypatch, [])
    fake_make = m.AdoClient

    def make(cfg):
        c = fake_make(cfg)
        if isinstance(who, AdoError):
            c.get = lambda path: (_ for _ in ()).throw(who)
        else:
            c._responses[0] = who
        return c
    monkeypatch.setattr(m, "AdoClient", make)
    assert m.main(["questions", "1001", "--post", "--apply"]) == 2
    assert "cannot tell which ADO comments are yours" in capsys.readouterr().err
    assert _writes(fake) == []


@pytest.mark.parametrize("who", [
    {"authenticatedUser": {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}},
    {"authenticatedUser": {"id": "11111111-2222-3333-4444-555555555555", "descriptor": "System:PublicAccess;aaa"}},
    {"authenticatedUser": {"id": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"}},
    {"authenticatedUser": {"id": " aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n"}},
    {"authenticatedUser": {"id": "11111111-2222-3333-4444-555555555555", "descriptor": " system:publicaccess;x"}}])
def test_me_rejects_anonymous_identity(who):
    with pytest.raises(ValueError, match="no valid credentials.*nothing posted") as ei:
        qmod._me(FakeClient(Config("contoso", "Fabrikam"), [who]))
    assert "aaaa" not in str(ei.value) and "PublicAccess" not in str(ei.value)
    ok = {"authenticatedUser": {"id": "11111111-2222-3333-4444-555555555555", "descriptor": "Microsoft.IdentityModel.x"}}
    assert qmod._me(FakeClient(Config("contoso", "Fabrikam"), [ok])) == "11111111-2222-3333-4444-555555555555"


def test_identity_is_read_org_level(tmp_path, monkeypatch):
    m, fake = _env(tmp_path, monkeypatch, [])
    assert m.main(["questions", "1001", "--post"]) == 0
    assert fake["c"].calls[0]["path"] == "/_apis/connectionData?api-version=7.1-preview"


def test_all_answered_edits_own_comment_then_is_unchanged(tmp_path, monkeypatch):
    done = "- [x] Q1: a → b · owner: PO\n"
    mine = f"{M}<br>Open questions for the product owner:<br>Q1: a"
    m, fake = _env(tmp_path, monkeypatch, [_cm(9, mine, 3)], body=done)
    assert m.main(["questions", "1001", "--post", "--apply"]) == 0
    assert _writes(fake)[0]["method"] == "PATCH"
    assert _writes(fake)[0]["body"] == {"text": f"{M}<br>All open questions are answered."}
    fake["list"][:] = [_cm(9, f"{M}\nAll open questions are answered.", 3)]
    assert m.main(["questions", "1001", "--post", "--apply"]) == 0 and _writes(fake) == []  # new client: no writes


def test_long_question_line_is_capped_and_marked(tmp_path, monkeypatch):
    m, fake = _env(tmp_path, monkeypatch, [], body=f"- [ ] Q1: {'x' * 600} → _(open)_\n")
    assert m.main(["questions", "1001", "--post", "--plan-out", "p.md"]) == 0
    plan = (tmp_path / "p.md").read_text()
    assert f"Q1 cut to {qmod.MAX_LINE} characters" in plan and "> Q1: " + "x" * 10 in plan
    line = next(x for x in plan.splitlines() if x.startswith("> Q1: "))[2:]
    assert len(line) == qmod.MAX_LINE and line.endswith("…")


def test_apply_and_expect_plan_flag_rules(tmp_path, monkeypatch, capsys):
    m, _ = _env(tmp_path, monkeypatch, [])
    for argv, msg in ((["questions", "1001", "--apply"], "--apply needs --post"),
                      (["questions", "1001", "--post", "--expect-plan", "p.md"], "--expect-plan needs --apply")):
        with pytest.raises(SystemExit) as e:
            m.main(argv)
        assert e.value.code == 2 and msg in capsys.readouterr().err


def test_hidden_section2_heading_and_questions_are_ignored():
    fenced = f"# DoD\n{SECTION2}\n- [ ] Q1: real → _(open)_ · owner: PO\n```\n## 3. AC\n- [ ] Q2: shown? → _(open)_\n```\n"
    assert [q["id"] for q in parse_questions(fenced)["open"]] == ["Q1"]


def test_comment_lists_assumptions_after_the_open_questions():
    parsed = parse_questions(_ledger(OPEN2 + "- [x] Q4: Archived too? → assumed: archived are excluded · owner: PO\n"
                                     "- [x] Q5: Cap? → 50 per page · owner: PO\n"))
    text = qmod.comment_html(parsed["open"], qmod._assumed(parsed["answered"]))
    assert text.startswith(BODY) and text.endswith(
        f"<br>{qmod._ASSUMED_HEAD}<br>Q4: Archived too? → assumed: archived are excluded")
    assert "Q5" not in text  # a question the PO answered is not an assumption
    only = qmod.comment_html([], qmod._assumed(parsed["answered"]))
    assert only == f"{M}<br>{qmod._ASSUMED_HEAD}<br>Q4: Archived too? → assumed: archived are excluded"


def test_own_comment_is_edited_to_show_assumptions_when_nothing_is_open(tmp_path, monkeypatch):
    own = f"{M}<br>Open questions for the product owner:<br>Q1: a"
    m, fake = _env(tmp_path, monkeypatch, [_cm(3, own, 5)],
                   body="- [x] Q1: a → assumed: yes · owner: PO\n")
    assert m.main(["questions", "1001", "--post", "--apply"]) == 0
    w = _writes(fake)
    assert len(w) == 1 and w[0]["method"] == "PATCH" and "assumed: yes" in w[0]["body"]["text"]
    assert "All open questions are answered" not in w[0]["body"]["text"]
