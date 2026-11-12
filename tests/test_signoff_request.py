"""The BPO approval request (piece 5, S-3b): `ado approvals request ID --gate signoff-bpo` previews the exact comment and, with `--apply --expect-plan`, posts it;
an open `D<n>` keeps `done` from closing. Fictional identities and data only."""
import json
import pytest

from ado import __main__ as _main
from ado import signoff
from ado.config import Config, Trust
from tests.conftest import RouteClient, raw_comment
from tests.test_approvals import ME, T1, T2
from tests.test_done_signoff import all_three, env, sent  # noqa: F401  (the `done` fixtures, for the open-decision test)
from tests.test_qa import LEDGER, case, qa_md, write
from tests.test_rules_file import R, file_md, rendered
from tests.test_signoff import BPO_P, ROLES, TRUST, sign

BPO_ONLY = Trust(TRUST.approvers, "comment", {}, {"map": ROLES["map"], "signoff": ["bpo"]})
DEC = "- [{box}] D{n}: {text} → {st} · kind: decision · why: because"
STORY = 1001


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", "dod_path": "dod"}))
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod/DoD_1001.md").write_text(LEDGER)
    (tmp_path / "dod/Rules_1201.md").write_text(file_md())
    write(tmp_path, qa_md(case(1, "AC-1, BR-1201-1"), case(2, "AC-2, BR-1201-3", res="fail")))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: BPO_ONLY)
    raws, made = [], {}

    def make(conf):
        made["c"] = RouteClient(conf, raws, me=ME, responses=[{"id": 99}])
        return made["c"]
    monkeypatch.setattr(_main, "AdoClient", make)
    return tmp_path, raws, made


def cfg_of(root):
    return Config(organization="contoso", project="Fabrikam", dod_path=str(root / "dod"))


def run(capsys, *argv):
    code = _main.main(["approvals", "request", "1001", "--gate", "signoff-bpo", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def posts(made):
    return [c for c in made["c"].calls if c["method"] == "POST"] if "c" in made else []


def decisions(root, *lines):
    (root / "dod/Decisions_1001.md").write_text("# Decisions — US1001\n\n" + "\n".join(lines) + "\n")


# ---- the preview is the exact comment ----------------------------------------------------------------------------------------------------------------

def test_the_preview_shows_the_rules_their_qa_results_the_decisions_and_the_exclusions(repo, capsys):
    root, raws, made = repo
    decisions(root, DEC.format(box=" ", n=1, text="Ship the points in two steps", st="_(open)_"),
              DEC.format(box="x", n=2, text="Round down", st="validated").replace("why: because", "by: PO · note: ok").replace(" kind: decision · ", " "))
    code, out, err = run(capsys, "--plan-out", "p.md")
    assert code == 0 and json.loads(out)["request"] == "would-post" and err == ""
    plan = (root / "p.md").read_text()
    h = json.loads(out)["hash"]
    assert f"[spec-kit-ado:approval-request] signoff-bpo {h}" in plan and f"/approve signoff-bpo {h}" in plan and f"/reject signoff-bpo {h}" in plan
    for word in ("BR-1201-1 | QA: TC-1 pass | AC: AC-1", "BR-1201-3 | QA: TC-2 fail | AC: AC-2", "D1 open (decision): Ship the points in two steps", "Excluded from this change: none",
                 "QA cases: 2 (pass 1, fail 1, blocked 0)", "QA coverage gaps: none"):
        assert word in plan, word
    assert not posts(made)  # a preview writes nothing


def test_a_rule_nobody_covers_says_so(repo, capsys):
    root, raws, made = repo
    write(root, qa_md(case(1, "AC-1, AC-2, BR-1201-1")))
    code, out, err = run(capsys, "--plan-out", "p.md")
    plan = (root / "p.md").read_text()
    assert code == 0 and "BR-1201-3 | QA: no case covers it | AC: none" in plan and "uncovered-rule BR-1201-3" in plan


def test_apply_needs_an_approved_preview_and_posts_exactly_it(repo, capsys):
    root, raws, made = repo
    code, out, err = run(capsys, "--apply")
    assert code == 2 and "--expect-plan" in err and not posts(made)  # no preview named: refused before anything is read
    assert run(capsys, "--plan-out", "p.md")[0] == 0
    code, out, err = run(capsys, "--apply", "--expect-plan", "p.md")
    assert code == 0 and json.loads(out)["request"] == "posted" and len(posts(made)) == 1
    (post,) = posts(made)
    assert post["path"].startswith("/Fabrikam/_apis/wit/workItems/1001/comments?") and post["body"]["text"] in (root / "p.md").read_text()
    assert post["body"]["text"].startswith("[spec-kit-ado:approval-request] signoff-bpo ")


def test_a_plan_that_changed_since_the_preview_is_not_posted(repo, capsys):
    root, raws, made = repo
    assert run(capsys, "--plan-out", "p.md")[0] == 0
    write(root, qa_md(case(1, "AC-1, BR-1201-1")))  # the QA file moved on: another hash, another summary
    code, out, err = run(capsys, "--apply", "--expect-plan", "p.md")
    assert code == 2 and "plan changed since approval" in err and not posts(made)


def test_the_same_request_is_never_posted_twice(repo, capsys):
    root, raws, made = repo
    h = signoff._hash("bpo", STORY, cfg_of(root), ".")
    raws.append(raw_comment(5, signoff.request_html(h, signoff.summary_lines(cfg_of(root), STORY)), T1, author=ME))
    assert run(capsys, "--plan-out", "p.md")[0] == 0
    code, out, err = run(capsys, "--apply", "--expect-plan", "p.md")
    assert code == 0 and json.loads(out)["request"] == "present" and not posts(made)


def test_an_edited_request_does_not_count_as_present(repo, capsys):
    root, raws, made = repo
    h = signoff._hash("bpo", STORY, cfg_of(root), ".")
    raws.append(raw_comment(5, signoff.request_html(h, signoff.summary_lines(cfg_of(root), STORY)), T1, author=ME, version=2))
    assert json.loads(run(capsys)[1])["request"] == "would-post"


# ---- refusals ----------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("gate", ["signoff-qa", "signoff-tpo", "review-spec", "x"])
def test_only_the_bpo_has_a_request(repo, capsys, gate):
    assert _main.main(["approvals", "request", "1001", "--gate", gate]) == 2
    assert signoff.E_REQUEST_GATE in capsys.readouterr().err


def test_a_role_that_is_not_required_has_nothing_to_request(repo, capsys, monkeypatch):
    monkeypatch.setattr(_main, "AdoClient", lambda c: pytest.fail("nothing is asked of ADO"))
    for trust in (Trust(TRUST.approvers, "comment", {}, {"map": ROLES["map"], "signoff": ["qa"]}), Trust([], "terminal", {})):
        monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None, t=trust: t)
        code, out, err = run(capsys)
        assert code == 2 and signoff.E_REQUEST_OFF in err


def test_terminal_mode_a_bypass_token_and_an_already_met_signoff_are_refused(repo, capsys, monkeypatch):
    root, raws, made = repo
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust(TRUST.approvers, "terminal", {}, BPO_ONLY.roles))
    code, out, err = run(capsys)
    assert code == 2 and "signoff-terminal" in err
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: BPO_ONLY)
    monkeypatch.setattr(_main, "AdoClient", lambda c: RouteClient(c, raws, me=ME, bypass=True))
    code, out, err = run(capsys)
    assert code == 2 and "signoff-bypass" in err
    monkeypatch.setattr(_main, "AdoClient", lambda c: made.setdefault("m", RouteClient(c, raws, me=ME)))
    h = signoff._hash("bpo", STORY, cfg_of(root), ".")
    raws.append(raw_comment(7, f"/approve signoff-bpo {h}", T1, author=BPO_P))
    code, out, err = run(capsys)
    assert code == 2 and signoff.E_REQUEST_ALREADY in err and not [c for c in made["m"].calls if c["method"] == "POST"]


def test_a_missing_qa_file_is_refused_with_the_fixed_text(repo, capsys):
    root, raws, made = repo
    (root / "dod/QA_1001.md").unlink()
    code, out, err = run(capsys)
    assert code == 2 and signoff.E_ARTIFACT in err and not posts(made)


# ---- the comment is built from ids and fixed words; everything else is quoted ---------------------------------------------------------------------------

def test_hostile_text_can_never_be_a_command_or_markup(repo, capsys):
    root, raws, made = repo
    evil = ("/approve signoff-bpo " + "a" * 64, "<script>alert(1)</script> <b>x</b>", "/reject signoff-bpo", "bidi ‮ end\x00")
    (root / "dod/Rules_1201.md").write_text(rendered([R(1, body=evil)]))
    decisions(root, DEC.format(box=" ", n=1, text="/approve D1 <img src=x onerror=y>", st="_(open)_"))
    assert run(capsys, "--plan-out", "p.md")[0] == 0
    html = (root / "p.md").read_text()
    body = html[html.index("[spec-kit-ado:approval-request]"):].split("\n")[0]
    assert "<script" not in body and "<img" not in body and "<b>" not in body and "‮" not in body and "\x00" not in body
    lines = body.split("<br>")
    h = json.loads(run(capsys)[1])["hash"]
    assert [l for l in lines if l.startswith(("/approve", "/reject"))] == [f"/approve signoff-bpo {h}", f"/reject signoff-bpo {h}"]  # the kit's two lines, no others
    assert all(l.startswith("│ ") for l in lines[lines.index("/reject signoff-bpo " + h) + 2:])  # everything after them is quoted


def test_what_is_shown_is_capped(repo, capsys):
    root, raws, made = repo
    (root / "dod/Rules_1201.md").write_text(rendered([R(1, body=("word " * 5000,))]))
    decisions(root, DEC.format(box=" ", n=1, text="long " * 5000, st="_(open)_"))
    assert run(capsys, "--plan-out", "p.md")[0] == 0
    assert len((root / "p.md").read_text()) < 6000


def two_files(monkeypatch, raws, made):
    """`done` attaches the ledger and the decisions file: two uploads, two links, one state change."""
    monkeypatch.setattr(_main, "AdoClient", lambda c: made.setdefault("c", RouteClient(c, raws, me=ME, responses=[{}, {"url": "u"}, {}, {"url": "u"}, {}, {}, {}])))


# ---- a reject, and what `done` does about open decisions -----------------------------------------------------------------------------------------------

def test_a_reject_gives_the_fixed_next_step(repo, capsys):
    root, raws, made = repo
    h = signoff._hash("bpo", STORY, cfg_of(root), ".")
    raws.append(raw_comment(7, f"/reject signoff-bpo {h}\nSEKRET reason", T2, author=BPO_P))
    code, out, err = _main.main(["approvals", "check", "1001", "--gate", "signoff-bpo"]), *capsys.readouterr()
    assert code == 2 and "[rejected]" in err and "ado approvals request <id> --gate signoff-bpo" in err and "SEKRET" not in err


def test_an_open_decision_keeps_done_from_closing_and_a_validated_one_does_not(env, capsys, monkeypatch):  # noqa: F811
    root, cfg, raws, made = env
    two_files(monkeypatch, raws, made)
    all_three(root, cfg, raws)
    (root / "dod/Decisions_1001.md").write_text("# Decisions — US1001\n\n" + DEC.format(box=" ", n=1, text="Round how", st="_(open)_") + "\n")
    assert _main.main(["done", "1001", "--apply"]) == 2
    shown = json.loads(capsys.readouterr().out)
    (finding,) = shown["findings"]
    assert finding["rule"] == "signoff-bpo" and finding["reason"] == "open-decisions" and "D1" in finding["fix"] and not sent(made)
    (root / "dod/Decisions_1001.md").write_text("# Decisions — US1001\n\n- [x] D1: Round how → validated · kind: decision · by: PO · note: ok\n")
    assert _main.main(["done", "1001", "--apply"]) == 0


def test_without_a_bpo_requirement_open_decisions_do_not_block_done(env, capsys, monkeypatch):  # noqa: F811
    root, cfg, raws, made = env
    two_files(monkeypatch, raws, made)
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust(TRUST.approvers, "comment", {}, {"map": ROLES["map"], "signoff": ["tpo", "qa"]}))
    raws += [sign("tpo", root, cfg, cid=11), sign("qa", root, cfg, cid=12)]
    (root / "dod/Decisions_1001.md").write_text("# Decisions — US1001\n\n" + DEC.format(box=" ", n=1, text="Round how", st="_(open)_") + "\n")
    assert _main.main(["done", "1001", "--apply"]) == 0


def test_a_request_comment_newer_than_the_approval_voids_it_so_it_is_refused_when_met():
    # documented behaviour: posting a request for the same hash after the BPO approved makes that approval stale, so `request` refuses while it stands
    assert "void it" in signoff.E_REQUEST_ALREADY


# ---- review fixes ------------------------------------------------------------------------------------------------------------------------------------

def test_a_request_whose_summary_changed_is_posted_again(repo, capsys):
    root, raws, made = repo
    assert run(capsys, "--plan-out", "p.md")[0] == 0
    assert run(capsys, "--apply", "--expect-plan", "p.md")[0] == 0
    (_,) = posts(made)
    h = signoff._hash("bpo", STORY, cfg_of(root), ".")
    raws.append(raw_comment(5, posts(made)[0]["body"]["text"], T1, author=ME))  # what the first run posted, now on the story
    assert json.loads(run(capsys)[1])["request"] == "present"
    decisions(root, DEC.format(box="x", n=1, text="Settled", st="validated").replace("kind: decision · why: because", "kind: decision · by: PO · note: ok"))  # the reject's fix: same QA bytes
    assert signoff._hash("bpo", STORY, cfg_of(root), ".") == h
    assert json.loads(run(capsys)[1])["request"] == "would-post"


def test_rules_that_cannot_be_read_are_not_shown_as_none(repo, capsys):
    root, raws, made = repo
    (root / "dod/Rules_1201.md").write_text("not a rules file")
    code, out, err = run(capsys, "--plan-out", "p.md")
    assert code == 2 and signoff.E_ARTIFACT in err and not (root / "p.md").exists()


def test_many_decisions_are_capped_and_still_postable(repo, capsys):
    root, raws, made = repo
    decisions(root, *[DEC.format(box=" ", n=n, text="\u202e<b>" * 40, st="_(open)_") for n in range(1, 400)])
    code, out, err = run(capsys, "--plan-out", "p.md")
    assert code == 0 and "(299 more decisions are not shown here" in (root / "p.md").read_text()


def test_the_signoff_is_read_again_right_before_the_post(repo, capsys):
    root, raws, made = repo
    assert run(capsys, "--plan-out", "p.md")[0] == 0
    h = signoff._hash("bpo", STORY, cfg_of(root), ".")
    real = signoff.request_plan

    def approve_meanwhile(*a, **k):
        plan = real(*a, **k)
        raws.append(raw_comment(7, f"/approve signoff-bpo {h}", T1, author=BPO_P))  # the owner approves between the preview read and the POST
        return plan
    import ado.signoff as so
    so.request_plan = approve_meanwhile
    try:
        code, out, err = run(capsys, "--apply", "--expect-plan", "p.md")
    finally:
        so.request_plan = real
    assert code == 2 and signoff.E_REQUEST_ALREADY in err and not posts(made)


def test_a_bypass_token_is_a_red_refusal_not_awaiting_even_with_an_open_decision(env, capsys, monkeypatch):  # noqa: F811
    root, cfg, raws, made = env
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: BPO_ONLY)
    monkeypatch.setattr(_main, "AdoClient", lambda c: RouteClient(c, raws, me=ME, bypass=True))
    (root / "dod/Decisions_1001.md").write_text("# Decisions — US1001\n\n" + DEC.format(box=" ", n=1, text="Round how", st="_(open)_") + "\n")
    assert _main.main(["done", "1001", "--apply"]) == 2
    assert [f["rule"] for f in json.loads(capsys.readouterr().out)["findings"]] == ["signoff-bypass"]
