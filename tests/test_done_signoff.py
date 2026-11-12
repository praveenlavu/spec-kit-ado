"""Role sign-offs at `ado done` (piece 5, S-3): every role in the target branch's `roles.signoff` must be met on the bytes now on disk, else exit 2 with
nothing sent; with `roles` absent `done` is what it was. Fictional identities and data only."""
import json
import subprocess

import pytest

from ado import __main__ as _main
from ado import signoff
from ado.config import Trust
from tests.conftest import RouteClient, ledger_md, raw_comment
from tests.test_approvals import ME, T1
from tests.test_signoff import BPO_P, QA_P, ROLES, TPO_P, TRUST, hash_of, sign, tree

APPLY = [{}, {"url": "u"}, {}, {}]  # work item GET, attachment upload, attach relation, state PATCH
WRITES = ("POST", "PATCH", "PUT", "DELETE")


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tree(tmp_path)
    (tmp_path / "dod/DoD_1001.md").write_text(ledger_md().replace("- [ ] x", "- [x] x"))  # section 5 ticked: ready to close
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", "dod_path": "dod"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: TRUST)
    raws, made = [], {}

    def make(conf, **kw):
        made["c"] = RouteClient(conf, raws, me=ME, responses=list(APPLY), **kw)
        return made["c"]
    monkeypatch.setattr(_main, "AdoClient", make)
    return tmp_path, cfg, raws, made


def all_three(root, cfg, raws):
    raws += [sign("tpo", root, cfg, cid=11), sign("qa", root, cfg, cid=12), sign("bpo", root, cfg, cid=13)]


def sent(made):
    return [c for c in made["c"].calls if c["method"] in WRITES]


def refused(capsys):
    out = capsys.readouterr()
    return json.loads(out.out), out.err


def test_all_met_closes_and_records_the_comment_ids_and_the_commit(env, capsys):
    root, cfg, raws, made = env
    all_three(root, cfg, raws)
    assert _main.main(["done", "1001", "--apply", "--plan-out", "p.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "Done" and out["signoffs"] == {"tpo": 11, "qa": 12, "bpo": 13} and out["bytes"] == "unknown"  # tmp tree: no git
    plan = (root / "p.md").read_text()
    for line in ("signoff tpo: comment 11", "signoff qa: comment 12", "signoff bpo: comment 13", "bytes: unknown"):
        assert line in plan, line
    assert "QA_1001" not in plan and not any("QA_1001" in str(c["body"]) for c in made["c"].calls)  # the QA file is never attached
    history = [op["value"] for c in sent(made) if c["method"] == "PATCH" and isinstance(c["body"], list) for op in c["body"] if op["path"] == "/fields/System.History"]
    assert history == ["Sign-offs: tpo comment 11; qa comment 12; bpo comment 13; bytes unknown"]


def test_the_commit_sha_is_recorded_when_there_is_one(env, capsys):
    root, cfg, raws, made = env
    all_three(root, cfg, raws)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "x"], cwd=root, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    assert _main.main(["done", "1001", "--plan-out", "p.md"]) == 0  # (the tree is untracked: not exactly that commit)
    assert f"bytes: {sha} (uncommitted changes" in (root / "p.md").read_text()
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "y"], cwd=root, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    capsys.readouterr()
    assert _main.main(["done", "1001", "--plan-out", "p.md"]) == 0
    assert f"bytes: {sha}\n" in (root / "p.md").read_text() and json.loads(capsys.readouterr().out)["bytes"] == sha


@pytest.mark.parametrize("apply", [True, False])
def test_nothing_signed_refuses_with_exit_2_and_sends_nothing(env, capsys, apply):
    root, cfg, raws, made = env
    assert _main.main(["done", "1001", *(["--apply"] * apply)]) == 2  # a dry run says so too
    shown, err = refused(capsys)
    assert shown["ok"] is False and [f["rule"] for f in shown["findings"]] == ["signoff-tpo", "signoff-qa", "signoff-bpo"]
    assert {f["reason"] for f in shown["findings"]} == {"missing"} and "signoff-tpo is not met on story 1001" in err
    assert not sent(made) and not (root / "p.md").exists()


def test_one_missing_role_names_only_that_one(env, capsys):
    root, cfg, raws, made = env
    raws += [sign("tpo", root, cfg, cid=11), sign("qa", root, cfg, cid=12)]
    assert _main.main(["done", "1001", "--apply"]) == 2
    assert [f["rule"] for f in refused(capsys)[0]["findings"]] == ["signoff-bpo"] and not sent(made)


def test_a_change_after_the_sign_off_makes_it_stale(env, capsys):
    root, cfg, raws, made = env
    all_three(root, cfg, raws)
    (root / "specs/001-x/spec.md").write_text("# Spec\nchanged after sign-off\n")
    assert _main.main(["done", "1001", "--apply"]) == 2
    (finding,) = refused(capsys)[0]["findings"]
    assert finding["rule"] == "signoff-tpo" and finding["reason"] == "stale" and not sent(made)


def test_ticking_tasks_after_the_sign_off_changes_nothing(env, capsys):
    root, cfg, raws, made = env
    all_three(root, cfg, raws)
    (root / "specs/001-x/tasks.md").write_text("- [x] T001 first\n")
    assert _main.main(["done", "1001", "--apply"]) == 0


def test_the_ledger_ticks_that_make_it_ready_do_not_void_a_signoff(env, capsys):
    root, cfg, raws, made = env
    all_three(root, cfg, raws)
    (root / "dod/DoD_1001.md").write_text(ledger_md().replace("- [ ] x", "- [x] x\n- [x] y"))  # what `done` reads last is not what was signed
    assert _main.main(["done", "1001", "--apply"]) == 0


def test_a_missing_qa_file_is_awaiting_not_a_crash(env, capsys):
    root, cfg, raws, made = env
    (root / "dod/QA_1001.md").unlink()
    raws.append(sign("tpo", root, cfg, cid=11))
    assert _main.main(["done", "1001", "--apply"]) == 2
    findings = refused(capsys)[0]["findings"]
    assert [(f["rule"], f["reason"]) for f in findings] == [("signoff-qa", "artifact"), ("signoff-bpo", "artifact")] and not sent(made)


def test_a_bypass_token_refuses_the_whole_gate_with_one_finding(env, capsys, monkeypatch):
    root, cfg, raws, made = env
    all_three(root, cfg, raws)
    monkeypatch.setattr(_main, "AdoClient", lambda conf: RouteClient(conf, raws, me=ME, bypass=True, responses=list(APPLY)))
    assert _main.main(["done", "1001", "--apply"]) == 2
    shown, err = refused(capsys)
    assert [f["rule"] for f in shown["findings"]] == ["signoff-bypass"] and err.startswith("error: strict check failed: signoff-bypass")


def test_terminal_mode_with_a_required_signoff_refuses(env, capsys, monkeypatch):
    root, cfg, raws, made = env
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust(TRUST.approvers, "terminal", {}, ROLES))
    assert _main.main(["done", "1001", "--apply"]) == 2
    assert [f["rule"] for f in refused(capsys)[0]["findings"]] == ["signoff-terminal"]


def test_strict_mode_refuses_the_same_way(env, capsys, monkeypatch):
    root, cfg, raws, made = env
    (root / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", "dod_path": "dod", "mode": "strict"}))
    monkeypatch.setattr(_main.strict, "check", lambda *a, **k: {"ok": True, "overrides_used": []})
    monkeypatch.setattr(_main, "_xcheck", lambda *a: ([], False))
    assert _main.main(["done", "1001", "--apply"]) == 2
    shown, _ = refused(capsys)
    assert shown["mode"] == "strict" and [f["rule"] for f in shown["findings"]] == ["signoff-tpo", "signoff-qa", "signoff-bpo"] and not sent(made)


def test_a_ledger_that_is_not_ready_is_judged_as_before(env, capsys, monkeypatch):
    root, cfg, raws, made = env
    monkeypatch.setattr(_main, "_xcheck", lambda *a: ([], False))  # (the decisions cross-check reads comments with approvers set; not this gate)
    (root / "dod/DoD_1001.md").write_text(ledger_md())  # section 5 not ticked
    assert _main.main(["done", "1001", "--apply"]) == 1
    assert json.loads(capsys.readouterr().out)["ready"] is False and not made["c"].calls


@pytest.mark.parametrize("trust", [Trust([], "terminal", {}), Trust(TRUST.approvers, "comment", {}, {"map": ROLES["map"], "signoff": []})])
def test_without_a_required_role_done_is_unchanged_and_reads_no_comments(env, capsys, monkeypatch, trust):
    root, cfg, raws, made = env
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: trust)
    monkeypatch.setattr(_main, "_xcheck", lambda *a: ([], False))
    assert _main.main(["done", "1001", "--apply"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "Done" and "signoffs" not in out and "bytes" not in out and "warnings" not in out
    assert not [c for c in made["c"].calls if "/comments" in c["path"] or c["path"].startswith("/_apis/permissions")]
    assert [c["method"] for c in made["c"].calls] == ["GET", "POST", "PATCH", "PATCH"]
    assert not [op for c in sent(made) if isinstance(c["body"], list) for op in c["body"] if op["path"] == "/fields/System.History"]


def test_a_preseeded_attachment_named_like_the_qa_file_changes_nothing(env, capsys, monkeypatch):
    root, cfg, raws, made = env
    wi = {"fields": {"System.State": "Active"}, "relations": [{"rel": "AttachedFile", "url": "https://x/_apis/wit/attachments/g?fileName=QA_1001.md",
                                                             "attributes": {"name": "QA_1001.md"}}]}
    monkeypatch.setattr(_main, "AdoClient", lambda conf: RouteClient(conf, raws, me=ME, responses=[wi] + APPLY[1:]))
    assert _main.main(["done", "1001", "--apply"]) == 2  # still refused: no sign-off comment exists
    assert [f["rule"] for f in refused(capsys)[0]["findings"]] == ["signoff-tpo", "signoff-qa", "signoff-bpo"]


def test_one_person_giving_two_signoffs_is_a_warning_not_a_block(env, capsys):
    root, cfg, raws, made = env
    raws += [sign("tpo", root, cfg, cid=11), sign("qa", root, cfg, cid=12, author=BPO_P), sign("bpo", root, cfg, cid=13)]  # BPO_P holds bpo and qa
    assert _main.main(["done", "1001", "--apply"]) == 0
    assert signoff.ONE_PERSON in json.loads(capsys.readouterr().out)["warnings"]


def test_three_people_give_no_warning(env, capsys):
    root, cfg, raws, made = env
    all_three(root, cfg, raws)
    assert _main.main(["done", "1001", "--apply"]) == 0
    assert "warnings" not in json.loads(capsys.readouterr().out)


def test_a_story_already_in_the_done_state_is_not_touched(env, capsys, monkeypatch):
    root, cfg, raws, made = env
    all_three(root, cfg, raws)
    wi = {"fields": {"System.State": "Done"}, "relations": [{"rel": "AttachedFile", "url": "u?fileName=DoD_1001.md", "attributes": {"name": "DoD_1001.md"}}]}
    clients = []
    monkeypatch.setattr(_main, "AdoClient", lambda conf: clients.append(RouteClient(conf, raws, me=ME, responses=[wi])) or clients[-1])
    assert _main.main(["done", "1001", "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["already_done"] is True and not [c for c in clients[0].calls if c["method"] in WRITES]


# ---- which target's roles judge it ------------------------------------------------------------------------------------------------------------------

def test_two_strict_sides_that_differ_in_roles_are_refused(monkeypatch):
    other = Trust(TRUST.approvers, "comment", {}, {"map": ROLES["map"], "signoff": ["bpo"]})
    monkeypatch.setattr(_main.strict, "default_base", lambda repo: "refs/remotes/origin/main")
    monkeypatch.setattr(_main.strict, "base_ref", lambda repo, base: "refs/remotes/origin/main")
    monkeypatch.setattr(_main.strict, "_resolve_base", lambda repo, ref: "abc")
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: other if base else TRUST)
    args = type("A", (), {"base": "main", "id": 1001})()
    client = type("C", (), {"config": type("K", (), {"mode": "advisory"})()})()
    with pytest.raises(_main.strict.Refused) as e:  # a refusal with a rule id: red in CI, never "could not evaluate"
        _main._signoffs(client, args)
    assert [f["rule"] for f in e.value.check["findings"]] == ["signoff-trust"]


def test_the_stricter_side_wins_when_only_one_requires_a_role(monkeypatch):
    none = Trust([], "terminal", {})
    monkeypatch.setattr(_main.strict, "default_base", lambda repo: "refs/remotes/origin/main")
    monkeypatch.setattr(_main.strict, "base_ref", lambda repo, base: "refs/remotes/origin/main")
    monkeypatch.setattr(_main.strict, "_resolve_base", lambda repo, ref: "abc")
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: TRUST if base else none)  # the merged commit dropped `roles`; the target still has it
    trust = _main._stricter_trust("main", lambda t: any(signoff.required(t, r) for r in signoff.ROLES), ci=True)[0]
    assert trust is TRUST
