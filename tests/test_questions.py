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


# ---- P4-10: `ado questions <id> --sync` (an approver's /answer Q<n>: text becomes the PO's answer, locally) and question age from SERVER time ----
import copy  # noqa: E402
import re  # noqa: E402
import datetime  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from ado import __main__ as _main  # noqa: E402
from ado import approvals, comments  # noqa: E402
from ado.config import Trust  # noqa: E402
from tests.conftest import comment_pages, connection_data, raw_comment, versions_response  # noqa: E402

TOK, KIT, APPR, OTHER = ("aaaa1111-0000-4000-8000-00000000000a", "4444444a-4444-4444-8444-44444444444b",
                         "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666")
T0, T1, T2, T3, T4 = (f"2026-10-01T{h}:00:00Z" for h in (10, 11, 12, 13, 14))
QA = "- [ ] Q1: Are contractors included? → _(open)_ · owner: PO"
QB = "- [ ] Q2: Which locale do we ship? → _(open)_ · owner: PO"
QC = "- [ ] Q3: Retention period? → _(open)_ · owner: PO"


def _at(s):
    return approvals._when(s)


def _sled(*lines):
    return _ledger("\n".join(lines) + "\n")


def _qopen(md):
    return parse_questions(md)["open"]


def _post_html(md):
    return qmod.comment_html(_qopen(md))


def _own(html, cid=10, date=T0, **kw):
    return raw_comment(cid, html, date, author=TOK, **kw)


def _ans(cid, text, date=T1, author=APPR, **kw):
    return raw_comment(cid, f"<p>{text}</p>", date, author=author, **kw)


def _rows(cfg, raws):
    found, truncated = comments.fetch(FakeClient(cfg, responses=comment_pages(raws)), 1001, with_meta=True)
    assert not truncated
    return found


def _ages(cfg, md, raws, vers, me=TOK):
    client = FakeClient(cfg, responses=[connection_data(me), vers])
    return qmod.ages(client, 1001, _qopen(md), _rows(cfg, raws)), client


def _qsync(cfg, md, raws, vers, approvers=(APPR,), excluded=(TOK,), hours=None, now=None, responses=()):
    rows = _rows(cfg, raws)
    ag = qmod.ages(FakeClient(cfg, responses=[connection_data(TOK), vers]), 1001, _qopen(md), rows)
    client = FakeClient(cfg, responses=list(responses))
    return qmod.sync(md, rows, list(approvers), set(excluded), client, 1001, now, hours, ag), client


def test_answer_synced_with_comment_id(cfg):
    md = _sled(QA, QB)
    html = _post_html(md)
    (new, res), client = _qsync(cfg, md, [_own(html), _ans(11, "/answer Q1: Yes, contractors count")], versions_response((1, html, T0)))
    assert res["answered"] == ["Q1"] and res["open"] == ["Q2"] and res["due"] == []
    assert res["skipped"] == {"Q2": "no-answer"} and res["bad_newer"] == {"Q1": 0, "Q2": 0}
    line = "- [x] Q1: Are contractors included? → Yes, contractors count (PO comment 11) · owner: PO"
    assert new == md.replace(QA, line)  # exactly that one line changed
    after = parse_questions(new)
    assert after["answered"] == [{"id": "Q1", "question": "Are contractors included?", "owner": "PO", "answer": "Yes, contractors count (PO comment 11)"}]
    assert [q["id"] for q in after["open"]] == ["Q2"] and after["unparsed"] is False
    assert all(c["method"] == "GET" for c in client.calls)  # a local write only: no ADO write, ever


def test_age_uses_full_line_not_qn_prefix(cfg):
    md = _sled(QB)
    html = _post_html(md)
    other = qmod.comment_html([{"id": "Q2", "question": "Which locale?", "owner": "PO"}])  # the same `Q2:` with other text
    assert _ages(cfg, md, [_own(html, version=2)], versions_response((1, other, T0), (2, html, T2)))[0] == {"Q2": _at(T2)}
    for text in (other, qmod.comment_html([{"id": "Q2", "question": "Which locale do we ship? And why", "owner": "PO"}]),
                 qmod.comment_html([{"id": "Q2", "question": "Which locale do we ship", "owner": "PO"}]),
                 qmod.comment_html([{"id": "Q2", "question": "Which locale do we ship?", "owner": "PO"}]).replace("Q2:", "Q22:")):
        assert _ages(cfg, md, [_own(text)], versions_response((1, text, T0)))[0] == {}  # not the line: no age, never a prefix match
    assert _ages(cfg, md, [_own(html)], versions_response((1, html.replace("Q2: ", "Q2:"), T0)))[0] == {}  # not the rendered line


def test_age_from_earliest_matching_version(cfg):
    md = _sled(QA, QB, QC)
    qs = _qopen(md)
    v1, v2, v3 = (qmod.comment_html(qs[:n]) for n in (1, 2, 3))
    ag, client = _ages(cfg, md, [_own(v3, version=3)], versions_response((1, v1, T0), (2, v2, T2), (3, v3, T3)))
    assert ag == {"Q1": _at(T0), "Q2": _at(T2), "Q3": _at(T3)}
    assert [c["method"] for c in client.calls] == ["GET", "GET"] and "/versions?api-version=7.1-preview.1" in client.calls[1]["path"]


def test_age_restarts_when_the_line_was_changed_and_changed_back(cfg):
    md = _sled(QB)
    a = _post_html(md)
    b = qmod.comment_html([{"id": "Q2", "question": "Which currency do we use?", "owner": "PO"}])
    raws = [_own(a, version=3)]
    assert _ages(cfg, md, raws, versions_response((1, a, T0), (2, b, T1), (3, a, T2)))[0] == {"Q2": _at(T2)}  # the run that ends at the current version
    assert _ages(cfg, md, raws, versions_response((1, a, T0), (2, a, T1), (3, a, T2)))[0] == {"Q2": _at(T0)}  # a no-op edit keeps the age
    assert _ages(cfg, md, [_own(b, version=3)], versions_response((1, a, T0), (2, a, T1), (3, b, T2)))[0] == {}  # the current version does not hold it


def test_a_version_with_markup_around_the_line_does_not_count(cfg):
    md = _sled(QA)
    hidden = f"{qmod.MARKER}<br><span style=\"display:none\">Q1: Are contractors included?</span>"
    assert _ages(cfg, md, [_own(hidden)], versions_response((1, hidden, T0)))[0] == {}
    html = _post_html(md)
    assert _ages(cfg, md, [_own(html, version=2)], versions_response((1, hidden, T0), (2, html, T2)))[0] == {"Q1": _at(T2)}
    bare = html.replace(qmod.MARKER + "<br>", "")  # a version that does not start with the questions marker is not the kit's comment
    assert _ages(cfg, md, [_own(html, version=2)], versions_response((1, bare, T0), (2, html, T2)))[0] == {"Q1": _at(T2)}


def test_a_cut_question_line_has_no_age_and_cannot_be_answered(cfg):
    md = _sled(f"- [ ] Q1: {'x' * 600} → _(open)_ · owner: PO")
    html = _post_html(md)
    assert _ages(cfg, md, [_own(html)], versions_response((1, html, T0)))[0] == {}
    (new, res), _ = _qsync(cfg, md, [_own(html), _ans(11, "/answer Q1: ok")], versions_response((1, html, T0)))
    assert new == md and res["answered"] == [] and res["skipped"] == {"Q1": "posting-lossy"}


def test_the_age_comes_from_the_newest_own_comment_only(cfg):
    md = _sled(QA)
    html = _post_html(md)
    raws = [_own(html, 5, T0), _own(html, 9, T2), raw_comment(7, html, T1, author=OTHER), raw_comment(8, html, T1, author=KIT)]
    assert _ages(cfg, md, raws, versions_response((1, html, T2)))[0] == {"Q1": _at(T2)}  # the older own comment, and other authors', are not read
    for only in ([raw_comment(7, html, T0, author=OTHER)], [raw_comment(7, "plain", T0, author=TOK)], []):
        with pytest.raises(ValueError, match="question age cannot be established") as e:
            _ages(cfg, md, only, versions_response((1, html, T0)))
        assert "no-comment" in str(e.value)


def test_ages_refuses_what_it_cannot_read(cfg):
    md = _sled(QA)
    html = _post_html(md)
    own = [_own(html)]
    cases = {"versions-shape": [{"count": 0, "value": []}, {"value": "x"}, "x", {"value": [{"version": 2, "text": html, "modifiedDate": T0}]},
                                versions_response((1, html, T0), (2, html, T1))],  # the comment row says version 1 but two exist
             "date-shape": [versions_response((1, html, "yesterday"))],
             "versions-unavailable": [AdoError("HTTP 404: SEKRET dana@contoso.example", status=404)]}
    for reason, answers in cases.items():
        for vers in answers:
            with pytest.raises(ValueError, match="question age cannot be established") as e:
                _ages(cfg, md, own, vers)
            assert reason in str(e.value) and "SEKRET" not in str(e.value) and "dana@" not in str(e.value), (reason, vers)
    with pytest.raises(ValueError, match="edited-by-other"):
        _ages(cfg, md, [_own(html, modified_by=OTHER)], versions_response((1, html, T0)))
    with pytest.raises(ValueError, match="no-identity"):
        _ages(cfg, md, own, versions_response((1, html, T0)), me="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    boom = FakeClient(cfg, responses=[AdoError("HTTP 401: SEKRET", status=401)])
    with pytest.raises(ValueError, match="no-identity") as e:
        qmod.ages(boom, 1001, _qopen(md), _rows(cfg, own))
    assert "SEKRET" not in str(e.value)
    with pytest.raises(AdoError):  # a server error is not an answer: it propagates (exit 1)
        qmod.ages(FakeClient(cfg, responses=[connection_data(TOK), AdoError("HTTP 500", status=500)]), 1001, _qopen(md), _rows(cfg, own))


def test_ages_without_open_questions_makes_no_request(cfg):
    md = _sled("- [x] Q1: a → b · owner: PO")
    ag, client = _ages(cfg, md, [], versions_response())
    assert ag == {} and client.calls == []


def test_no_versions_refuses_assume(cfg):
    md = _sled(QA)
    html = _post_html(md)
    with pytest.raises(ValueError, match="question age cannot be established"):
        _qsync(cfg, md, [_own(html)], {"count": 0, "value": []}, hours=24, now=_at("2026-10-05T00:00:00Z"))
    with pytest.raises(ValueError, match="question age cannot be established"):  # no own comment: there is no `asked:` fallback
        _qsync(cfg, md, [], versions_response((1, html, T0)), hours=24, now=_at("2026-10-05T00:00:00Z"))
    assert "asked:" not in Path(qmod.__file__).read_text(encoding="utf-8").split("def ages")[1].split("\ndef ")[0]  # no ledger-stored fallback


def _due(cfg, md, hours, now, extra=()):
    html = _post_html(md)
    return _qsync(cfg, md, [_own(html), *extra], versions_response((1, html, T0)), hours=hours, now=now)[0]


def test_due_uses_server_time_only(cfg, monkeypatch):
    md = _sled(QA, QB)
    server = _at("2026-10-02T12:00:00Z")  # 26 hours after the posting
    real = time.time
    out = []
    for shift in (0, -864000, 864000):  # the LOCAL clock 10 days back / forward changes nothing
        monkeypatch.setattr(time, "time", lambda s=shift: real() + s)
        out.append(_due(cfg, md, 24, server)[1])
    assert out[0] == out[1] == out[2] and out[0]["due"] == ["Q1", "Q2"]
    assert _due(cfg, md, 24, _at("2026-10-02T10:00:00Z"))[1]["due"] == ["Q1", "Q2"]  # exactly 24 hours: due
    assert _due(cfg, md, 24, _at("2026-10-02T09:59:59Z"))[1]["due"] == []
    assert _due(cfg, md, None, server)[1]["due"] == []  # no assume_after_hours: nothing is ever due
    src = Path(qmod.__file__).read_text(encoding="utf-8")
    for needle in (r"(?<![a-z])time\.(time|monotonic|localtime|gmtime)\(", r"datetime\.now", "utcnow", r"date\.today", r"^import time", r"\.now\(\)", "localtime"):
        assert not re.search(needle, src, re.M), needle


def test_no_server_time_refuses_assume(cfg):
    md = _sled(QA)
    with pytest.raises(ValueError, match="server time") as e:
        _due(cfg, md, 24, None)
    assert "assume" in str(e.value)
    assert _due(cfg, md, None, None)[1]["due"] == []  # without `assume_after_hours` the clock is not needed
    for bad in (0, 721, True, "24", 1.5):
        with pytest.raises(ValueError):
            _due(cfg, md, bad, _at(T4))
    with pytest.raises(ValueError, match="server time"):
        _due(cfg, md, 24, datetime.datetime(2026, 10, 5))  # a naive datetime is not server time


def test_due_never_closes_question(cfg):
    md = _sled(QA, QB)
    new, res = _due(cfg, md, 1, _at("2026-11-01T00:00:00Z"))
    assert res["due"] == ["Q1", "Q2"] and res["open"] == ["Q1", "Q2"] and new == md  # a due question stays open: only `plan --close-assumed` closes it
    answered = _due(cfg, md, 1, _at("2026-11-01T00:00:00Z"), [_ans(11, "/answer Q1: yes")])
    assert answered[1]["answered"] == ["Q1"] and answered[1]["due"] == ["Q2"] and "assumed" not in answered[0]


def test_only_an_eligible_approvers_command_counts(cfg):
    md = _sled(QA)
    html = _post_html(md)
    cases = {"token": _ans(11, "/answer Q1: x", author=TOK), "kit": _ans(11, "/answer Q1: x", author=KIT),
             "non-approver": _ans(11, "/answer Q1: x", author=OTHER), "on-behalf": _ans(11, "/answer Q1: x", on_behalf=OTHER),
             "quoted": raw_comment(11, "<blockquote>/answer Q1: x</blockquote><p>agreed</p>", T1, author=APPR),
             "second-line": raw_comment(11, "<p>thanks</p><p>/answer Q1: x</p>", T1, author=APPR),
             "approve-verb": _ans(11, "/approve D1"), "no-text": _ans(11, "/answer Q1"), "hash": _ans(11, f"/answer Q1 {'a' * 64}: x"),
             "lower-q": _ans(11, "/answer q1: x"), "other-target": _ans(11, "/answer Q2: x"), "zero-padded": _ans(11, "/answer Q01: x")}
    for name, c in cases.items():
        (new, res), _ = _qsync(cfg, md, [_own(html), c], versions_response((1, html, T0)), excluded=(TOK, KIT))
        assert new == md and res["answered"] == [], name
    edited = raw_comment(11, "<p>/answer Q1: y</p>", T1, author=APPR, version=2)
    (new, res), _ = _qsync(cfg, md, [_own(html), edited], versions_response((1, html, T0)), excluded=(TOK, KIT),
                           responses=[versions_response((1, "<p>/answer Q1: x</p>", T1), (2, "<p>/answer Q1: y</p>", T2))])
    assert new == md and res["answered"] == []  # edited after the fact


def test_answer_must_be_newer_than_the_question_s_posting_version(cfg):
    md = _sled(QB)
    a = _post_html(md)
    b = qmod.comment_html([{"id": "Q2", "question": "Which currency do we use?", "owner": "PO"}])
    # the ledger line is the reworded one: the answer to the OLD wording (T1) is older than the version that holds the new line (T2)
    new_md = _sled("- [ ] Q2: Which currency do we use? → _(open)_ · owner: PO")
    raws = lambda *a_: [_own(b, version=2), *a_]  # noqa: E731
    vers = versions_response((1, a, T0), (2, b, T2))
    (new, res), _ = _qsync(cfg, new_md, raws(_ans(11, "/answer Q2: EUR", T1)), vers)
    assert new == new_md and res["skipped"] == {"Q2": "older-than-posting"}
    (new, res), _ = _qsync(cfg, new_md, raws(_ans(11, "/answer Q2: EUR", T2)), vers)  # a tie loses
    assert new == new_md and res["skipped"] == {"Q2": "older-than-posting"}
    (new, res), _ = _qsync(cfg, new_md, raws(_ans(11, "/answer Q2: EUR", T3)), vers)
    assert res["answered"] == ["Q2"] and "EUR (PO comment 11)" in new
    # before the question was posted at all
    (new, res), _ = _qsync(cfg, md, [_own(a), _ans(11, "/answer Q2: early", "2026-10-01T09:00:00Z")], versions_response((1, a, T0)))
    assert res["skipped"] == {"Q2": "older-than-posting"}
    # a question that was never posted (not in the kit's comment) cannot be answered
    (new, res), _ = _qsync(cfg, _sled(QA, QB), [_own(qmod.comment_html(_qopen(_sled(QA)))), _ans(11, "/answer Q2: x")],
                           versions_response((1, qmod.comment_html(_qopen(_sled(QA))), T0)))
    assert new == _sled(QA, QB) and res["skipped"]["Q2"] == "not-posted"


def test_the_newest_valid_answer_wins_and_malformed_later_lines_are_counted(cfg):
    md = _sled(QA)
    html = _post_html(md)
    vers = versions_response((1, html, T0))
    raws = [_own(html), _ans(11, "/answer Q1: first", T1), _ans(12, "/answer Q1: second", T2), _ans(13, "/answer Q1: third", T3, author=OTHER)]
    (new, res), _ = _qsync(cfg, md, raws, vers)
    assert "second (PO comment 12)" in new and "first" not in new and "third" not in new and res["bad_newer"] == {"Q1": 0}
    (new, res), _ = _qsync(cfg, md, raws + [_ans(14, "/answer Q1 no colon", T4)], vers)
    assert "second (PO comment 12)" in new and res["bad_newer"] == {"Q1": 1}  # a later retraction typed wrong is counted, never ignored
    (new, res), _ = _qsync(cfg, md, [_own(html), _ans(11, "/answer Q1: first", T2), _ans(12, "/answer Q1: tie", T2)], vers)
    assert "tie (PO comment 12)" in new  # equal times: the higher comment id is the newer one


def test_only_an_open_question_is_answered_and_a_later_answer_does_not_replace(cfg):
    done = "- [x] Q2: Which locale? → en-GB only (PO comment 5) · owner: PO"
    typed = "- [ ] Q4: Export format? → CSV · owner: Dev"
    ticked = "- [x] Q5: Cap? → _(open)_ · owner: PO"
    md = _sled(QA, done, typed, ticked)
    html = _post_html(md)
    raws = [_own(html), _ans(11, "/answer Q2: other", T1), _ans(12, "/answer Q4: JSON", T1), _ans(13, "/answer Q5: fifty", T1), _ans(14, "/answer Q9: nobody", T1)]
    (new, res), _ = _qsync(cfg, md, raws, versions_response((1, html, T0)))
    assert res["answered"] == ["Q5"] and new == md.replace(ticked, "- [x] Q5: Cap? → fifty (PO comment 13) · owner: PO")
    assert res["skipped"] == {"Q1": "no-answer", "Q4": "has-answer"} and "Q2" not in res["bad_newer"] and "Q9" not in str(res)


def test_the_answer_text_is_made_safe_and_reads_back(cfg):
    md = _sled(QA, QB)
    html = _post_html(md)
    vers = versions_response((1, html, T0))
    hostile = ("yes → no · owner: Dev | x &lt;!-- hidden --&gt; ``` ~~~ dana@contoso.example (PO comment 99) -&gt; z")
    (new, res), _ = _qsync(cfg, md, [_own(html), _ans(11, f"/answer Q1: {hostile}")], vers)
    line = [x for x in new.splitlines() if "Q1:" in x][0]
    assert res["answered"] == ["Q1"] and line.startswith("- [x] Q1: Are contractors included? → yes - no - owner: Dev - x")
    assert line.count("→") == 1 and line.count("·") == 1 and "|" not in line and line.count("(PO comment") == 1 and line.endswith("(PO comment 11) · owner: PO")
    for bad in ("<!--", "-->", "```", "~~~", "dana@", "->", "PO comment 99"):
        assert bad not in line, bad
    after = parse_questions(new)
    assert [q["id"] for q in after["answered"]] == ["Q1"] and after["answered"][0]["owner"] == "PO" and [q["id"] for q in after["open"]] == ["Q2"]
    (new, _res), _ = _qsync(cfg, md, [_own(html), _ans(11, "/answer Q1: Assumed: skip it")], vers)
    assert "→ PO answer: Assumed: skip it (PO comment 11)" in new and qmod._assumed(parse_questions(new)["answered"]) == []  # a PO's answer is never an assumption
    (new, _res), _ = _qsync(cfg, md, [_own(html), _ans(11, "/answer Q1: " + "w " * 400)], vers)
    assert len([x for x in new.splitlines() if "Q1:" in x][0]) < 700  # capped


def test_a_line_a_rewrite_would_damage_blocks_the_sync(cfg):
    inline = "- [ ] Q1: Are contractors included? <!-- keep --> → _(open)_ · owner: PO"
    md = _sled(inline, QB)
    html = _post_html(md)
    with pytest.raises(ValueError, match="Q1.*nothing written"):
        _qsync(cfg, md, [_own(html), _ans(11, "/answer Q1: yes"), _ans(12, "/answer Q2: de-DE")], versions_response((1, html, T0)))
    long = _sled(" " * 1300 + f"- [ ] Q1: {'q' * 480} → _(open)_ · owner: PO")  # 1,300 blanks of indentation: the answer would push the line over the cap
    html = _post_html(long)
    with pytest.raises(ValueError, match="Q1.*nothing written"):
        _qsync(cfg, long, [_own(html), _ans(11, "/answer Q1: " + "a " * 250)], versions_response((1, html, T0)))


def test_a_malformed_ledger_is_refused(cfg):
    html = qmod.comment_html([{"id": "Q1", "question": "a", "owner": "PO"}])
    bodies = {"unclosed fence": QA + "\n```\n- [ ] Q2: hidden → _(open)_\n", "unclosed comment": QA + "\n<!--\n", "duplicate id": QA + "\n" + QA,
              "duplicate after answer": "- [x] Q1: a → b · owner: PO\n" + QA, "non-ascii digit": QA + "\n- [ ] Q٢: x → _(open)_",
              "blank run": "- [ ] Q2:" + " " * 100000 + "x"}
    for name, body in bodies.items():
        md = _sled(body)
        t0 = time.monotonic()
        with pytest.raises(ValueError, match="malformed.*nothing written"):
            qmod.check_ledger(md)
        with pytest.raises(ValueError, match="malformed"):
            qmod.sync(md, _rows(cfg, [_own(html)]), [APPR], {TOK}, FakeClient(cfg), 1001, None, None, {})
        assert time.monotonic() - t0 < 1, name
    with pytest.raises(ValueError, match="malformed"):
        qmod.check_ledger("# DoD\nno section two\n")
    assert qmod.check_ledger(_sled(QA))["open"][0]["id"] == "Q1"


def test_sync_keeps_crlf_and_changes_only_the_target_line(cfg):
    md = _sled(QA, QB).replace("\n", "\r\n")
    html = _post_html(md)
    (new, res), _ = _qsync(cfg, md, [_own(html), _ans(11, "/answer Q2: de-DE")], versions_response((1, html, T0)))
    assert new == md.replace(QB, "- [x] Q2: Which locale do we ship? → de-DE (PO comment 11) · owner: PO") and "\n" not in new.replace("\r\n", "")
    assert res["answered"] == ["Q2"]


def test_apply_sync_refuses_a_changed_or_symlinked_file(tmp_path, cfg):
    md = _sled(QA)
    html = _post_html(md)
    rows = _rows(cfg, [_own(html), _ans(11, "/answer Q1: yes")])
    ag = {"Q1": _at(T0)}
    args = (rows, [APPR], {TOK}, FakeClient(cfg), 1001, None, None, ag)
    f = tmp_path / "DoD_1001.md"
    f.write_bytes(md.encode())
    res = qmod.apply_sync(f, md, *args)
    assert res["answered"] == ["Q1"] and "(PO comment 11)" in f.read_text(encoding="utf-8")
    f.write_bytes((md + "extra\n").encode())  # the file changed since it was read
    with pytest.raises(ValueError, match="nothing written"):
        qmod.apply_sync(f, md, *args)
    assert f.read_bytes() == (md + "extra\n").encode()
    real = tmp_path / "real.md"
    real.write_bytes(md.encode())
    link = tmp_path / "link.md"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="regular file"):
        qmod.apply_sync(link, md, *args)
    assert real.read_bytes() == md.encode()
    big = tmp_path / "big.md"
    big.write_bytes(b"x" * ((1 << 20) + 1))
    with pytest.raises(ValueError, match="regular file"):
        qmod.apply_sync(big, md, *args)


# ---- the verb ----
def _qenv(tmp_path, monkeypatch, md, raws, vers=None, approvers=(APPR,), hours=None, now=None, bypass=False, comment_responses=None, me=None, ages_me=None,
          extra=()):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    (tmp_path / "dod").mkdir()
    led = tmp_path / "dod" / "DoD_1001.md"
    led.write_bytes(md.encode("utf-8"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust(list(approvers), "comment", {"assume_after_hours": hours, "approval_requests": False}))
    proj = {"id": "99999999-9999-4999-8999-999999999999", "name": "Fabrikam"}
    html = _post_html(md) if "Q1" in md else ""
    vers = versions_response((1, html, T0)) if vers is None else vers
    resp = ([proj, {"count": 1, "value": [bypass]}] + (comment_pages(raws) if comment_responses is None else comment_responses)
            + [me or connection_data(TOK), ages_me or connection_data(TOK), vers] + list(extra))
    seen = {}

    def make(c):
        seen["c"] = FakeClient(c, responses=copy.deepcopy(resp))
        seen["c"].now = now
        return seen["c"]
    monkeypatch.setattr(_main, "AdoClient", make)
    return led, seen


def test_sync_verb_prints_answered_open_due_and_bad_newer(tmp_path, monkeypatch, capsys):
    md = _sled(QA, QB, QC)
    html = _post_html(md)
    raws = [_own(html), _ans(11, "/answer Q1: yes"), _ans(12, "/answer Q3 oops", T2)]
    led, seen = _qenv(tmp_path, monkeypatch, md, raws, hours=24, now=_at("2026-10-02T12:00:00Z"))
    assert _main.main(["questions", "1001", "--sync"]) == 0
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["id"] == 1001 and out["answered"] == ["Q1"] and out["open"] == ["Q2", "Q3"] and out["due"] == ["Q2", "Q3"]
    assert out["bad_newer"] == {"Q1": 0, "Q2": 0, "Q3": 1} and any(n.startswith("Q3: 1 later malformed command line(s) by an approver") for n in out["notice"])
    assert "contractors" not in cap.out and "locale" not in cap.out and cap.err == ""  # ids only, never question or answer text
    assert not any(c["method"] != "GET" for c in seen["c"].calls)
    got = parse_questions(led.read_text(encoding="utf-8"))
    assert [q["id"] for q in got["answered"]] == ["Q1"] and [q["id"] for q in got["open"]] == ["Q2", "Q3"]  # due questions stay open
    assert led.read_text(encoding="utf-8") == md.replace(QA, "- [x] Q1: Are contractors included? → yes (PO comment 11) · owner: PO")


def test_sync_verb_without_assume_after_hours_needs_no_clock(tmp_path, monkeypatch, capsys):
    md = _sled(QA)
    led, _ = _qenv(tmp_path, monkeypatch, md, [_own(_post_html(md))], now=None)
    assert _main.main(["questions", "1001", "--sync"]) == 0
    assert json.loads(capsys.readouterr().out)["due"] == [] and led.read_text(encoding="utf-8") == md


def test_sync_verb_refuses_assume_without_server_time(tmp_path, monkeypatch, capsys):
    md = _sled(QA)
    raws = [_own(_post_html(md)), _ans(11, "/answer Q1: yes")]
    led, seen = _qenv(tmp_path, monkeypatch, md, raws, hours=24, now=None)
    assert _main.main(["questions", "1001", "--sync"]) == 2
    err = capsys.readouterr().err
    assert "server time" in err and "assume" in err and led.read_bytes() == md.encode()  # nothing written, not even the answer
    (tmp_path / "b").mkdir()
    led, seen = _qenv(tmp_path / "b", monkeypatch, md, raws, hours=24, now=_at(T4))

    def boom():
        raise AdoError("HTTP 500: SEKRET dana@contoso.example", status=500)
    monkeypatch.setattr(FakeClient, "server_now", lambda self: boom())
    assert _main.main(["questions", "1001", "--sync"]) == 2
    err = capsys.readouterr().err
    assert "server time" in err and "SEKRET" not in err and "dana@" not in err and led.read_bytes() == md.encode()


def test_sync_verb_refuses_without_versions_even_without_assume(tmp_path, monkeypatch, capsys):
    md = _sled(QA)
    raws = [_own(_post_html(md)), _ans(11, "/answer Q1: yes")]
    for n, (vers, comment_rows) in enumerate(((AdoError("HTTP 404: SEKRET", status=404), raws), ({"count": 0, "value": []}, raws), (None, [_ans(11, "/answer Q1: yes")]))):
        (tmp_path / str(n)).mkdir()
        led, _ = _qenv(tmp_path / str(n), monkeypatch, md, comment_rows, vers=vers, hours=None, now=None)
        assert _main.main(["questions", "1001", "--sync"]) == 2, n
        err = capsys.readouterr().err
        assert "question age cannot be established" in err and "SEKRET" not in err and led.read_bytes() == md.encode(), n
    (tmp_path / "ok").mkdir()
    led, _ = _qenv(tmp_path / "ok", monkeypatch, md, raws, vers=AdoError("HTTP 500: SEKRET", status=500), hours=None, now=None)
    assert _main.main(["questions", "1001", "--sync"]) == 1 and led.read_bytes() == md.encode()  # a server error is exit 1


def test_sync_verb_refused_when_bypass_bit(tmp_path, monkeypatch, capsys):
    md = _sled(QA)
    raws = [_own(_post_html(md)), _ans(11, "/answer Q1: yes")]
    for n, bypass in enumerate((True, None)):
        (tmp_path / str(n)).mkdir()
        led, seen = _qenv(tmp_path / str(n), monkeypatch, md, raws, bypass=bypass)
        assert _main.main(["questions", "1001", "--sync"]) == 2
        assert approvals.BYPASS_REFUSAL in capsys.readouterr().err and led.read_bytes() == md.encode()
        assert not any("/comments" in c["path"] for c in seen["c"].calls)  # refused before the comments are read


def test_sync_verb_refusals_exit_2_write_nothing_and_echo_no_ado_text(tmp_path, monkeypatch, capsys):
    md = _sled(QA)
    good = [_own(_post_html(md)), _ans(11, "/answer Q1: yes")]
    tok = {"comments": [raw_comment(1, "x", T0)], "continuationToken": "same"}
    cases = {"no_approvers": dict(approvers=()), "truncated": dict(comment_responses=[tok, tok]),
             "unreadable_page": dict(comment_responses=[{"comments": "SEKRET dana@contoso.example"}]),
             "unreadable_shape": dict(comment_responses=[{"comments": [{"id": "SEKRET"}]}]),
             "unknown_token_identity": dict(me=AdoError("HTTP 401: SEKRET dana@contoso.example", status=401)),
             "anonymous_token": dict(me=connection_data("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
             "ages_identity_lost": dict(ages_me=AdoError("HTTP 401: SEKRET dana@contoso.example", status=401))}
    for name, kw in cases.items():
        (tmp_path / name).mkdir()
        led, _ = _qenv(tmp_path / name, monkeypatch, md, good, **kw)
        assert _main.main(["questions", "1001", "--sync"]) == 2, name
        err = capsys.readouterr().err
        assert err.startswith("error:") and "SEKRET" not in err and "dana@" not in err and led.read_bytes() == md.encode(), name
    for name, body in (("bad_file", _sled(QA, "- [ ] Q2: broken", "```")), ("dup_file", _sled(QA, QA))):
        (tmp_path / name).mkdir()
        led, _ = _qenv(tmp_path / name, monkeypatch, body, good)
        assert _main.main(["questions", "1001", "--sync"]) == 2
        assert "malformed" in capsys.readouterr().err and led.read_bytes() == body.encode()
    (tmp_path / "missing").mkdir()
    led, _ = _qenv(tmp_path / "missing", monkeypatch, md, good)
    led.unlink()
    assert _main.main(["questions", "1001", "--sync"]) == 2 and "ledger" in capsys.readouterr().err
    (tmp_path / "linked").mkdir()
    led, _ = _qenv(tmp_path / "linked", monkeypatch, md, good)
    real = led.with_name("real.md")
    real.write_bytes(led.read_bytes())
    led.unlink()
    led.symlink_to(real)
    assert _main.main(["questions", "1001", "--sync"]) == 2
    assert "[x]" not in real.read_text(encoding="utf-8")


def test_sync_verb_http_error_is_exit_1_and_writes_nothing(tmp_path, monkeypatch, capsys):
    md = _sled(QA)
    led, _ = _qenv(tmp_path, monkeypatch, md, [], comment_responses=[AdoError("HTTP 500: SEKRET", status=500)])
    assert _main.main(["questions", "1001", "--sync"]) == 1 and led.read_bytes() == md.encode()


def test_sync_verb_with_all_questions_answered_needs_no_versions(tmp_path, monkeypatch, capsys):
    md = _sled("- [x] Q1: a → b · owner: PO")
    led, seen = _qenv(tmp_path, monkeypatch, md, [], vers=AdoError("HTTP 404", status=404), hours=24, now=_at(T4))
    assert _main.main(["questions", "1001", "--sync"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["answered"] == out["open"] == out["due"] == [] and led.read_bytes() == md.encode()


def test_sync_flag_combinations_exit_2(capsys):
    for extra in (["--post"], ["--apply"], ["--plan-out", "p.md"], ["--expect-plan", "p.md"]):
        with pytest.raises(SystemExit) as e:
            _main.main(["questions", "1001", "--sync", *extra])
        assert e.value.code == 2, extra
        capsys.readouterr()


def test_sync_verb_uses_the_real_server_clock_not_the_local_one(tmp_path, monkeypatch, capsys):
    from ado.client import AdoClient
    monkeypatch.setattr(FakeClient, "server_now", AdoClient.server_now)  # the real method: response `Date` + monotonic elapsed
    md = _sled(QA, QB)
    raws = [_own(_post_html(md)), _ans(11, "/answer Q1: yes")]
    real, outs = time.time, []
    for n, (shift, date) in enumerate(((0, "Fri, 02 Oct 2026 12:00:00 GMT"), (-864000, "Fri, 02 Oct 2026 12:00:00 GMT"), (864000, "Fri, 02 Oct 2026 12:00:00 GMT"),
                                       (0, "Fri, 02 Oct 2026 09:00:00 GMT"))):
        monkeypatch.setattr(time, "time", lambda s=shift: real() + s)
        (tmp_path / str(n)).mkdir()
        led, seen = _qenv(tmp_path / str(n), monkeypatch, md, raws, hours=24)
        make = _main.AdoClient

        def withdate(c, make=make, date=date):
            fake = make(c)
            fake.last_date, fake._date_mono = date, time.monotonic()
            return fake
        monkeypatch.setattr(_main, "AdoClient", withdate)
        assert _main.main(["questions", "1001", "--sync"]) == 0
        outs.append(json.loads(capsys.readouterr().out)["due"])
    assert outs == [["Q2"], ["Q2"], ["Q2"], []]  # 26 h after the posting is due whatever the local clock says; 23 h is not
    (tmp_path / "bad").mkdir()
    led, _ = _qenv(tmp_path / "bad", monkeypatch, md, raws, hours=24)
    make = _main.AdoClient

    def garbage(c):
        fake = make(c)
        fake.last_date = "not a date"
        return fake
    monkeypatch.setattr(_main, "AdoClient", garbage)
    assert _main.main(["questions", "1001", "--sync"]) == 2 and "server time" in capsys.readouterr().err and led.read_bytes() == md.encode()


def test_a_newer_answer_on_an_answered_question_is_listed_as_ignored_never_applied(cfg):
    done_none = "- [x] Q2: Which locale? → assumed: no · owner: PO"
    done_same = "- [x] Q3: Cap? → 50 (PO comment 11) · owner: PO"
    done_other = "- [x] Q4: Export? → CSV (PO comment 5) · owner: PO"
    done_hand = "- [x] Q5: Retention? → 30 days · owner: PO"
    md = _sled(QA, done_none, done_same, done_other, done_hand)
    html = _post_html(md)
    raws = [_own(html), _ans(11, "/answer Q3: 50"), _ans(12, "/answer Q2: yes they are", T2), _ans(13, "/answer Q4: JSON", T2), _ans(14, "/answer Q5: 60", T2, author=OTHER)]
    (new, res), _ = _qsync(cfg, md, raws, versions_response((1, html, T0)))
    assert new == md and res["ignored"] == ["Q2", "Q4"]  # Q3 names that very comment; Q5's command is a non-approver's; nothing is replaced
    assert res["answered"] == [] and "yes they are" not in str(res) and "JSON" not in str(res)
    (new, res), _ = _qsync(cfg, md, raws[:2], versions_response((1, html, T0)))
    assert res["ignored"] == []
    (new, res), _ = _qsync(cfg, md, [*raws[:2], _ans(15, "/answer Q3: 99", T3)], versions_response((1, html, T0)))
    assert res["ignored"] == ["Q3"]  # a NEWER valid answer than the one the line names


def test_sync_verb_prints_a_notice_for_an_ignored_answer(tmp_path, monkeypatch, capsys):
    md = _sled(QA, "- [x] Q2: Which locale? → assumed: no · owner: PO")
    led, _ = _qenv(tmp_path, monkeypatch, md, [_own(_post_html(md)), _ans(12, "/answer Q2: yes they are")])
    assert _main.main(["questions", "1001", "--sync"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ignored"] == ["Q2"] and "Q2: answered already: a later /answer was not applied; correct the ledger by hand" in out["notice"]
    assert led.read_bytes() == md.encode() and "yes they are" not in json.dumps(out)


def test_a_line_broken_by_an_entity_or_newline_inside_a_version_is_not_shown(cfg):
    md = _sled(QA)
    html = _post_html(md)
    line = "Q1: Are contractors included?"
    for evil in (f"{qmod.MARKER}<br>Q1: x&#10;{line}", f"{qmod.MARKER}<br>Q1: x\n{line}".replace("\n", "\n"), f"{qmod.MARKER}<br>x&#13;{line}"):
        assert _ages(cfg, md, [_own(html, version=2)], versions_response((1, evil, T0), (2, html, T2)))[0] == {"Q1": _at(T2)}, evil
    assert _ages(cfg, md, [_own(html, version=2)], versions_response((1, f"{qmod.MARKER}<br>{line}", T0), (2, html, T2)))[0] == {"Q1": _at(T0)}
