# F5 (P4-20 slice A, M-1..M-4): trust-core minors. One test group per finding, each with its honest flow.
import json

import pytest

from ado import __main__ as _main
from ado import approvals, comments, decisions
from ado import questions as qmod
from ado.config import ConfigInvalid, Trust, trusted
from tests.conftest import FakeClient, RouteClient, raw_comment, versions_response
from tests.test_approvals import HUMAN, ME, T0, T1, T2, T3, T12, QA, _cmd, _wait
from tests.test_config import G1, TRUST_BASE, _trust_repo
from tests.test_decisions import APPR, KITID, S1, T_V1, T_V2, TOK, _kit, _posted, _rows, _sync, _sync_env, _vote

# ---- M-1: a question whose NEWEST posting shows it cut stays open at wait-po-answers, as a decision does at wait-po-validation ----------------
LONGQ = {"id": "Q1", "question": "Should the export include " + "archived and deleted items " * 30 + "?"}
SHORTQ = {"id": "Q1", "question": "Include archived items?"}


def _qcomment(q, date, version=1, modified=None):
    html = qmod.comment_html([q])
    return html, raw_comment(10, html, date, author=ME, version=version, modifiedDate=modified or date)


def test_a_cut_question_stays_open_until_a_short_repost_is_answered(cfg):
    assert qmod._line(LONGQ)[1] is True
    html, cut = _qcomment(LONGQ, T0)
    v1 = versions_response((1, html, T0))
    assert _wait(cfg, [cut], v1)[0] == {"met": False, "open": ["Q1"], "bad_newer": 0, "cut": ["Q1"]}
    assert _wait(cfg, [cut, _cmd(11, "/answer Q1: yes", T1)], v1)[0] == {"met": False, "open": ["Q1"], "bad_newer": 0, "cut": ["Q1"]}  # was: met
    short_html, short = _qcomment(SHORTQ, T0, version=2, modified=T2)
    v2 = versions_response((1, html, T0), (2, short_html, T2))
    assert _wait(cfg, [short, _cmd(11, "/answer Q1: yes", T1)], v2)[0]["open"] == ["Q1"]  # an answer older than the short repost is no answer
    assert _wait(cfg, [short, _cmd(11, "/answer Q1: yes", T1), _cmd(12, "/answer Q1: yes", T3)], v2)[0] == {"met": True, "open": [], "bad_newer": 0}


def test_a_cut_question_is_not_closed_by_the_assume_window_either(cfg):
    import datetime
    html, cut = _qcomment(LONGQ, T0)
    st = _wait(cfg, [cut], versions_response((1, html, T0)), trust=Trust([HUMAN], "comment", {"assume_after_hours": 1}),
               now=datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc))[0]
    assert st["open"] == ["Q1"]


def test_sync_and_the_gate_agree_on_a_cut_question(cfg):
    html, cut = _qcomment(LONGQ, T0)
    md = "# DoD\n\n## 2. Brainstorm & plan\n\n- [ ] Q1: %s → _(open)_ · owner: PO\n\n## 3. AC\n" % LONGQ["question"]
    rows = _rows(cfg, [cut, _cmd(11, "/answer Q1: yes", T1, author=HUMAN)])
    ages = qmod.ages(FakeClient(cfg, responses=[{"authenticatedUser": {"id": ME}}, versions_response((1, html, T0))]), 1001, qmod.check_ledger(md)["open"], rows)
    _, rep = qmod.sync(md, rows, [HUMAN], {ME.lower()}, FakeClient(cfg), 1001, None, None, ages)
    assert rep["skipped"] == {"Q1": "posting-lossy"} and rep["answered"] == []  # sync and gate: both leave it open


# ---- M-2: `decisions --sync` reports a verdict whose `via` comment no longer counts -----------------------------------------------------------------
def _synced(cfg):
    md, html = _posted(S1)
    return _sync(cfg, md, [_kit(html), _vote(11, "/approve D1")])[0][0], html


def test_sync_reports_a_verdict_whose_via_comment_was_edited(cfg):
    synced, html = _synced(cfg)
    edited = [_kit(html), _vote(11, "/reject D1: no", T_V1, version=2, modified=T_V2)]
    (new, res), _ = _sync(cfg, synced, edited, responses=[versions_response((1, "<p>/approve D1</p>", T_V1), (2, "<p>/reject D1: no</p>", T_V2))])
    assert new == synced and res["skipped"] == {"D1": "no-verdict"} and res["no_longer_valid"] == ["D1"]


def test_sync_reports_a_verdict_whose_approver_was_removed(cfg):
    synced, html = _synced(cfg)
    (new, res), _ = _sync(cfg, synced, [_kit(html), _vote(11, "/approve D1")], approvers=("66666666-6666-4666-8666-666666666666",))
    assert new == synced and res["no_longer_valid"] == ["D1"]


def test_sync_honest_flows_have_no_such_report(cfg):
    synced, html = _synced(cfg)
    (new, res), _ = _sync(cfg, synced, [_kit(html), _vote(11, "/approve D1")])  # the comment still counts
    assert "no_longer_valid" not in res and res["skipped"] == {"D1": "up-to-date"}
    (new, res), _ = _sync(cfg, synced, [_kit(html), _vote(11, "/approve D1"), _vote(12, "/reject D1: no", T_V2)])  # a newer valid command: rewritten, not lost
    assert "no_longer_valid" not in res and res["applied"] == ["D1"]
    md, html = _posted(S1)
    assert "no_longer_valid" not in _sync(cfg, md, [_kit(html)])[0][1]  # an open line with no verdict yet: nothing was lost


def test_sync_verb_notes_the_lost_approval_on_stderr_exit_0(cfg, tmp_path, monkeypatch, capsys):
    synced, html = _synced(cfg)
    raws = [_kit(html), _vote(11, "/reject D1: no", T_V1, version=2, modified=T_V2)]
    led, _ = _sync_env(tmp_path, monkeypatch, synced, raws, extra=[versions_response((1, "<p>/approve D1</p>", T_V1), (2, "<p>/reject D1: no</p>", T_V2))])
    assert _main.main(["decisions", "1001", "--sync"]) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["no_longer_valid"] == ["D1"] and cap.err.strip() == "approval-no-longer-valid: D1"
    assert led.read_text(encoding="utf-8") == synced


# ---- M-3: identity ids are compared case-insensitively ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("me,author", [(ME, ME.lower()), (ME.lower(), ME)])
def test_plan_post_finds_its_own_comment_whatever_the_case(cfg, me, author):
    old = qmod.comment_html([SHORTQ])
    client = RouteClient(cfg, [raw_comment(10, old, T0, author=author)], me=me)
    plan = qmod.plan_post(client, 1001, {"open": [SHORTQ, {"id": "Q2", "question": "Which locale?"}], "answered": []})
    assert plan["action"] == "edit" and plan["comment_id"] == 10  # was: add (a second marker comment)
    assert qmod.own_comment(client, comments.fetch(client, 1001, with_meta=True)[0])["id"] == 10  # the P4-10 path agrees


def test_plan_post_still_ignores_another_identity(cfg):
    other = RouteClient(cfg, [raw_comment(10, qmod.comment_html([SHORTQ]), T0, author=HUMAN)], me=ME)
    assert qmod.plan_post(other, 1001, {"open": [SHORTQ], "answered": []})["action"] == "add"


# ---- M-4: trust keys in a non-root $ADO_CONFIG file are refused, not dropped ----------------------------------------------------------------------
def _nonroot(repo, data, monkeypatch, name="config/ado.json"):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    monkeypatch.setenv("ADO_CONFIG", str(p))
    return p


@pytest.mark.parametrize("key,val", [("approvers", [G1]), ("approval", "comment"), ("unattended", {"assume_after_hours": 24})])
def test_a_non_root_config_with_a_trust_key_is_invalid(tmp_path, git_env, monkeypatch, key, val):
    repo = _trust_repo(tmp_path, {**TRUST_BASE, "approvers": [G1], "approval": "comment"})
    _nonroot(repo, {**TRUST_BASE, key: val}, monkeypatch)
    with pytest.raises(ConfigInvalid) as e:
        trusted(str(repo))
    assert str(e.value) == approvals_text() and G1 not in str(e.value)
    with pytest.raises(ConfigInvalid):
        trusted(str(repo), base="origin/main")


def test_a_non_root_config_without_trust_keys_and_the_root_file_keep_working(tmp_path, git_env, monkeypatch):
    repo = _trust_repo(tmp_path, {**TRUST_BASE, "approvers": [G1], "approval": "comment"})
    _nonroot(repo, {**TRUST_BASE, "dod_path": "dod"}, monkeypatch)
    assert trusted(str(repo)).approvers == [G1]  # unchanged: the target's keys, the non-root file is only the rest of the config
    monkeypatch.setenv("ADO_CONFIG", str(repo / "ado.config.json"))  # the root file itself may carry trust keys (it is read from the target)
    (repo / "ado.config.json").write_text(json.dumps({**TRUST_BASE, "approvers": [G1], "approval": "comment"}))
    assert trusted(str(repo)).approvers == [G1]
    monkeypatch.setenv("ADO_CONFIG", str(repo / "config" / ".." / "ado.config.json"))  # the same file by another spelling
    assert trusted(str(repo)).approval == "comment"


def test_a_non_root_config_with_a_trust_key_is_invalid_with_no_origin_head_too(tmp_path, git_env, monkeypatch):
    repo = _trust_repo(tmp_path, TRUST_BASE, origin=False)
    _nonroot(repo, {**TRUST_BASE, "approvers": [G1]}, monkeypatch)
    with pytest.raises(ConfigInvalid) as e:
        trusted(str(repo))
    assert str(e.value) == approvals_text()


def approvals_text():
    from ado.config import E_NONROOT_TRUST
    return E_NONROOT_TRUST


def test_the_gate_text_says_why_a_cut_question_stays_open(cfg):
    html, cut = _qcomment(LONGQ, T0)
    with pytest.raises(approvals.ApprovalMissing) as e:
        approvals.check(RouteClient(cfg, [cut, _cmd(11, "/answer Q1: yes", T1)], me=ME, responses=[versions_response((1, html, T0))]),
                        Trust([HUMAN], "comment", {}), 1001, "wait-po-answers", cfg)
    assert str(e.value) == (approvals.E_WAIT.format(gate="wait-po-answers", story=1001, n=1, ids="Q1", bad="") + approvals.E_CUT_OPEN.format(ids="Q1"))
    assert "last shown cut" in str(e.value)


def test_a_question_that_vanished_after_being_shown_cut_stays_open(cfg):
    """m-2: the last showing counts, as for decisions: an /answer given to the cut text must not close it once the comment drops the question."""
    q2 = {"id": "Q2", "question": "Which locale?"}
    h1, h2 = qmod.comment_html([LONGQ, q2]), qmod.comment_html([q2])
    own = raw_comment(10, h2, T0, author=ME, version=2, modifiedDate=T2)
    vers = versions_response((1, h1, T0), (2, h2, T2))
    answers = [_cmd(11, "/answer Q1: yes", T1), _cmd(12, "/answer Q2: en", T3)]
    assert _wait(cfg, [own] + answers, vers)[0] == {"met": False, "open": ["Q1"], "bad_newer": 0, "cut": ["Q1"]}
    # honest flow: shown cut, then short, then dropped: the LAST showing was short, so an answer closes it as before
    h_short = qmod.comment_html([SHORTQ, q2])
    own3 = raw_comment(10, h2, T0, author=ME, version=3, modifiedDate=T3)
    vers3 = versions_response((1, h1, T0), (2, h_short, T1), (3, h2, T3))
    assert _wait(cfg, [own3, _cmd(11, "/answer Q1: yes", T2), _cmd(12, "/answer Q2: en", T3)], vers3)[0] == {"met": True, "open": [], "bad_newer": 0}


# ---- m-1: every way a synced verdict stops being backed is reported -------------------------------------------------------------------------------
def _lost(cfg):
    """A sync wrote `via: 11` with an approver's earlier verdict (9) older than the posting still in the thread; comment 11 is then edited into a reject."""
    md, html = _posted(S1)
    pre = [_vote(9, "/approve D1", "2026-10-01T09:00:00Z")]
    synced = _sync(cfg, md, pre + [_kit(html), _vote(11, "/approve D1")])[0][0]
    assert "via: 11" in synced
    edited = pre + [_kit(html), _vote(11, "/reject D1: no", T_V1, version=2, modified=T_V2)]
    return _sync(cfg, synced, edited, responses=[versions_response((1, "<p>/approve D1</p>", T_V1), (2, "<p>/reject D1: no</p>", T_V2))])[0]


def test_an_older_verdict_remaining_does_not_hide_the_lost_via(cfg):
    new, res = _lost(cfg)
    assert res["skipped"] == {"D1": "older-than-posting"} and res["no_longer_valid"] == ["D1"]  # was: silent


def test_a_via_line_skipped_for_any_reason_is_reported_but_up_to_date_and_renewed_are_not(cfg):
    md, html = _posted(S1)
    synced = _sync(cfg, md, [_kit(html), _vote(11, "/approve D1")])[0][0]
    # a posting rule (here posting-edited) shows in `skipped` already and is not reported (report decision 1)
    (new, res), _ = _sync(cfg, synced, [_kit(html, version=2, modified_by=APPR), _vote(11, "/approve D1")])
    assert res["skipped"]["D1"] == "posting-edited" and "no_longer_valid" not in res
    # bad-comment-id: the newest valid verdict has a non-decimal comment id
    (new, res), _ = _sync(cfg, synced, [_kit(html), _vote("x1", "/approve D1")])
    assert res["skipped"] == {"D1": "bad-comment-id"} and res["no_longer_valid"] == ["D1"]
    (new, res), _ = _sync(cfg, synced, [_kit(html), _vote(11, "/approve D1")])
    assert "no_longer_valid" not in res and res["skipped"] == {"D1": "up-to-date"}


def test_the_root_file_is_recognised_however_it_is_reached(tmp_path, git_env, monkeypatch):
    repo = _trust_repo(tmp_path, {**TRUST_BASE, "approvers": [G1], "approval": "comment"})
    (repo / "svc").mkdir()
    monkeypatch.setenv("ADO_CONFIG", str(repo / "ado.config.json"))  # m-3 (b): the root file by an absolute path, run from a subdirectory
    assert trusted(str(repo / "svc")).approvers == [G1]
    upper = repo / "ADO.CONFIG.JSON"
    if upper.exists():  # m-3 (a): a case-insensitive disk (the macOS default) reaches the root file in any letter case
        monkeypatch.setenv("ADO_CONFIG", str(upper))
        assert trusted(str(repo)).approvers == [G1]


def test_a_subproject_config_with_trust_keys_is_refused_without_adoconfig_too(tmp_path, git_env, monkeypatch):
    """m-3 (c): `svc/ado.config.json` read by `Config.load` from `svc/` is not the file the target is read from."""
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    repo = _trust_repo(tmp_path, TRUST_BASE)
    svc = repo / "svc"
    svc.mkdir()
    (svc / "ado.config.json").write_text(json.dumps({**TRUST_BASE, "approvers": [G1], "approval": "comment"}))
    for base in (None, "origin/main"):
        with pytest.raises(ConfigInvalid) as e:
            trusted(str(svc), base=base)
        assert str(e.value) == approvals_text()
    (svc / "ado.config.json").write_text(json.dumps(TRUST_BASE))  # without trust keys it works as before
    assert trusted(str(svc)).approvers == [] and trusted(str(svc), base="origin/main").approval == "terminal"


def test_the_refusal_is_exit_2_for_the_verbs_that_read_the_keys_and_gate_is_unaffected(tmp_path, git_env, monkeypatch, capsys):
    """I-1: `gate`, `strict mode` and `branch --id-from` never read the approval keys, so a non-root file with them keeps the PR check green."""
    repo = _trust_repo(tmp_path, TRUST_BASE)
    (repo / "dod").mkdir()
    (repo / "dod" / "DoD_1001.md").write_text("## 3. AC\n", encoding="utf-8")
    other = _nonroot(repo, {**TRUST_BASE, "approvers": [G1], "approval": "comment"}, monkeypatch)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ADO_TOKEN", "t")
    assert _main.main(["gate", "1001", "--offline", "--src", str(repo)]) in (0, 1)
    assert "error:" not in capsys.readouterr().err
    for argv in (["decisions", "1001", "--sync"], ["questions", "1001", "--sync"]):
        assert _main.main(argv) == 2
        assert capsys.readouterr().err.strip() == "error: " + approvals_text() and str(other) not in approvals_text()
