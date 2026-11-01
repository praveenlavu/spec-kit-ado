import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ado.__main__ import dispatch, main, _fields
from tests.conftest import FakeClient, commit, git, ledger_md, review_md


def test_fields_parses_key_value_pairs():
    assert _fields(["System.AreaPath=MC", "Custom.X=1"]) == {
        "System.AreaPath": "MC",
        "Custom.X": "1",
    }


def test_fields_without_equals_is_value_error():
    with pytest.raises(ValueError, match="--field expects Name=Value: oops"):
        _fields(["System.Title=ok", "oops"])


def test_fields_empty_value_allowed():
    assert _fields(["System.Tags="]) == {"System.Tags": ""}


def test_main_bad_field_exits_2_before_any_request(tmp_path, monkeypatch, capsys):
    (tmp_path / "ado.config.json").write_text(
        json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr("ado.client._OPENER.open",
                        lambda *a, **k: pytest.fail("request sent"))
    assert main(["update", "1", "--field", "oops"]) == 2
    assert "--field expects Name=Value: oops" in capsys.readouterr().err


def test_gate_offline_needs_no_token(tmp_path, monkeypatch, capsys):
    (tmp_path / "ado.config.json").write_text(
        json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_1001.md").write_text("## 3. AC\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.delenv("ADO_TOKEN", raising=False)

    def boom(*a, **k):
        raise AssertionError("token lookup attempted")
    monkeypatch.setattr("ado.auth.load_token", boom)
    monkeypatch.setattr("ado.auth.auth_header", boom)
    monkeypatch.setattr("ado.client.auth_header", boom)
    assert main(["gate", "1001", "--offline", "--src", str(tmp_path)]) in (0, 1)
    assert "error:" not in capsys.readouterr().err


def test_dispatch_get(cfg):
    client = FakeClient(cfg, responses=[{"id": 42}])

    class Args:
        command, id = "get", 42

    assert dispatch(client, Args()) == {"id": 42}
    assert client.calls[0]["method"] == "GET"


def test_dispatch_comments_writes_out_file(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = FakeClient(cfg, responses=[{"comments": [{"text": "<p>note</p>", "createdDate": "2026-10-01T00:00:00Z"}]}])

    class Args:
        command, id, out = "comments", 2, ".ado/c.md"

    assert dispatch(client, Args()) == [{"date": "2026-10-01T00:00:00Z", "text": "note"}]
    assert client.calls[0]["method"] == "GET"
    assert "## 2026-10-01T00:00:00Z" in (tmp_path / ".ado" / "c.md").read_text()


def test_main_without_config_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    assert main(["get", "1"]) == 2
    assert "ado setup" in capsys.readouterr().err


def test_main_missing_token_exits_2(tmp_path, monkeypatch, capsys):
    (tmp_path / "ado.config.json").write_text(
        json.dumps({"organization": "contoso", "project": "Fabrikam"})
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: empty_home)

    assert main(["get", "1"]) == 2
    assert "error:" in capsys.readouterr().err


def test_module_run_guard_exits_0_and_prints_version():
    repo_root = Path(__file__).resolve().parents[1]
    # src/ on PYTHONPATH so the smoke test works in any environment
    # (installed or not), not just this repo's own .venv.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in [str(repo_root / "src"), env.get("PYTHONPATH", "")] if p
    )
    result = subprocess.run(
        [sys.executable, "-m", "ado", "--version"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    from ado import __version__
    assert result.stdout.strip() == __version__


def test_dod_command_writes_ledger(cfg, tmp_path, monkeypatch):
    from ado import dod as dodmod
    from ado.__main__ import dispatch

    story = {"id": 1001, "fields": {"System.Title": "T",
             "Microsoft.VSTS.Common.AcceptanceCriteria": "AC one"}}
    client = FakeClient(cfg, responses=[story])
    client.config.dod_path = str(tmp_path)

    class Args:
        command, id, force, refresh, dir = "dod", 1001, False, False, None

    out = dispatch(client, Args())
    assert out["action"] == "created"
    assert dodmod.dod_file(str(tmp_path), 1001).exists()
    assert "AC one" in (tmp_path / "DoD_1001.md").read_text()


def test_dod_command_reports_thin_ac_in_json(cfg, tmp_path):
    """AC-1: `ado dod` surfaces ac_thin/ac_chars so an agent can branch on it."""
    from ado.__main__ import dispatch

    story = {"id": 1001, "fields": {"System.Title": "T",
             "Microsoft.VSTS.Common.AcceptanceCriteria": "<div>too short</div>"}}
    client = FakeClient(cfg, responses=[story])
    client.config.dod_path = str(tmp_path)

    class Args:
        command, id, force, refresh, dir = "dod", 1001, False, False, None

    out = dispatch(client, Args())
    assert out["ac_thin"] is True
    assert out["ac_chars"] == len("too short")


def test_dod_force_and_refresh_actions(cfg, tmp_path):
    from ado.__main__ import dispatch
    story = {"id": 1001, "fields": {"System.Title": "T",
             "Microsoft.VSTS.Common.AcceptanceCriteria": "AC"}}

    class A:
        command, id, dir, force, refresh = "dod", 1001, None, False, False

    c1 = FakeClient(cfg, responses=[story]); c1.config.dod_path = str(tmp_path)
    assert dispatch(c1, A())["action"] == "created"

    class AF(A):
        force = True
    c2 = FakeClient(cfg, responses=[story]); c2.config.dod_path = str(tmp_path)
    assert dispatch(c2, AF())["action"] == "overwritten"

    class AR(A):
        refresh = True
    c3 = FakeClient(cfg, responses=[story]); c3.config.dod_path = str(tmp_path)
    assert dispatch(c3, AR())["action"] == "refreshed"


def test_main_dod_exists_exits_2(cfg, tmp_path, monkeypatch, capsys):
    import json as _json
    from ado import __main__ as m
    from ado.dod import DodExists
    (tmp_path / "ado.config.json").write_text(_json.dumps({"organization": "o", "project": "p"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.setattr(m.workitems, "get", lambda client, wid: {"id": wid, "fields": {}})

    def boom(*a, **k):
        raise DodExists("boom")

    monkeypatch.setattr(m.dod, "write_dod", boom)
    assert m.main(["dod", "1001"]) == 2
    assert "error:" in capsys.readouterr().err


def test_gate_cli_missing_ledger_is_advisory(cfg, tmp_path, monkeypatch, capsys):
    import json as _json
    from ado.__main__ import dispatch

    class Args:
        command, id, src, changed = "gate", 42, str(tmp_path), []
    client = FakeClient(cfg)
    client.config.dod_path = str(tmp_path)   # no DoD_42.md here
    out = dispatch(client, Args())
    assert out["blocking"] is False
    assert out["ledger"] == "missing"


def test_gate_cli_skips_unreadable_changed_file(cfg, tmp_path):
    from ado.__main__ import dispatch
    (tmp_path / "DoD_7.md").write_text("## 3. AC\n", encoding="utf-8")

    class Args:
        command, id, src, changed = "gate", 7, str(tmp_path), [str(tmp_path / "gone.cls")]
    client = FakeClient(cfg)
    client.config.dod_path = str(tmp_path)
    out = dispatch(client, Args())      # must NOT raise (changed file does not exist)
    assert out["blocking"] is False


def test_cli_dod_spec_dir_flag(tmp_path, monkeypatch):
    import json as _json
    from ado import __main__ as m
    (tmp_path / "ado.config.json").write_text(_json.dumps({"organization": "o", "project": "p"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.setattr(m.workitems, "get", lambda client, wid: {"id": wid, "fields": {}})
    seen = []
    monkeypatch.setattr(m.dod, "write_dod", lambda *a, **k: seen.append(k.get("spec_dir")) or tmp_path / "x.md")
    assert m.main(["dod", "1001", "--spec-dir", "specs/001-x"]) == 0
    assert m.main(["dod", "1001"]) == 0
    assert seen == ["specs/001-x", ""]


def test_cli_dod_multiline_spec_dir_exits_2(tmp_path, monkeypatch, capsys):
    import json as _json
    from ado import __main__ as m
    (tmp_path / "ado.config.json").write_text(_json.dumps({"organization": "o", "project": "p"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.setattr(m.workitems, "get", lambda client, wid: {"id": wid, "fields": {}})
    assert m.main(["dod", "1001", "--spec-dir", "a\nb"]) == 2
    assert "error:" in capsys.readouterr().err
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["ado.config.json"]


def _push_env(tmp_path, monkeypatch, responses):
    import json as _json
    from ado import __main__ as m
    from ado.client import AdoClient
    (tmp_path / "ado.config.json").write_text(
        _json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    fake = {}

    def make(cfg):
        fake["c"] = FakeClient(cfg, responses=list(responses))
        return fake["c"]
    monkeypatch.setattr(m, "AdoClient", make)
    return m, fake


def test_cli_push_from_ledger_spec_dir(tmp_path, monkeypatch, capsys):
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_1001.md").write_text("- **Spec dir:** specs/001-x\n")
    (tmp_path / "specs" / "001-x").mkdir(parents=True)
    (tmp_path / "specs" / "001-x" / "tasks.md").write_text("- [ ] T001 Do it\n")
    m, fake = _push_env(tmp_path, monkeypatch, [{"workItems": []}])
    assert m.main(["push", "1001"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [o["action"] for o in out["ops"]] == ["create"]
    assert [c["method"] for c in fake["c"].calls] == ["POST"]  # dry run: WIQL only


def test_cli_push_no_source_exits_2(tmp_path, monkeypatch, capsys):
    m, _ = _push_env(tmp_path, monkeypatch, [])
    assert m.main(["push", "1001"]) == 2
    assert m.main(["push", "1001", "--from", "nope.md"]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_plan_out_written(tmp_path, monkeypatch, capsys):
    (tmp_path / "tasks.md").write_text("- [ ] T001 Do it\n")
    m, _ = _push_env(tmp_path, monkeypatch, [{"workItems": []}])
    assert m.main(["push", "1001", "--from", "tasks.md", "--plan-out", "out/plan.md"]) == 0
    assert "create 1 · update 0" in (tmp_path / "out" / "plan.md").read_text()


def test_cli_push_apply_failed_exits_1(tmp_path, monkeypatch, capsys):
    from ado.client import AdoError
    (tmp_path / "tasks.md").write_text("- [ ] T001 Do it\n")
    m, fake = _push_env(tmp_path, monkeypatch, [{"workItems": []}])
    monkeypatch.setattr(m.push.workitems, "create",
                        lambda *a, **k: (_ for _ in ()).throw(AdoError("boom")))
    assert m.main(["push", "1001", "--from", "tasks.md", "--apply"]) == 1
    assert json.loads(capsys.readouterr().out)["failed"][0]["task_id"] == "T001"


def test_cli_push_duplicate_task_ids_exits_2_without_calls(tmp_path, monkeypatch, capsys):
    (tmp_path / "tasks.md").write_text("- [ ] T001 A\n- [ ] T001 B\n")
    m, fake = _push_env(tmp_path, monkeypatch, [])
    assert m.main(["push", "1001", "--from", "tasks.md", "--apply"]) == 2
    assert "T001" in capsys.readouterr().err
    assert fake["c"].calls == []


# ---- H5: --summary-out ----
def _gate_repo(tmp_path, monkeypatch, block=()):
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "gate": {"block": list(block)}}))
    (tmp_path / "dod").mkdir()
    from ado.dod import SECTION2
    (tmp_path / "dod" / "DoD_1001.md").write_text(
        f"{SECTION2}\n## 3. AC\n| AC | x | tests | ev | status |\n|---|---|---|---|---|\n| AC-1 | x | Gone.testX | | red |\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.delenv("ADO_TOKEN", raising=False)


@pytest.mark.parametrize("block,code", [((), 0), (("ac_traceability",), 1)])
def test_main_gate_summary_out_written(tmp_path, monkeypatch, capsys, block, code):
    _gate_repo(tmp_path, monkeypatch, block)
    p = tmp_path / "out" / "gate.md"
    assert main(["gate", "1001", "--offline", "--src", str(tmp_path), "--summary-out", str(p)]) == code
    md = p.read_text(encoding="utf-8")
    assert "`AC-1`" in md and ("**Blocking" in md) == bool(block)
    assert json.loads(capsys.readouterr().out)["blocking"] is bool(block)


def test_main_gate_missing_config_writes_could_not_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    p = tmp_path / "gate.md"
    assert main(["gate", "1001", "--summary-out", str(p)]) == 2
    assert "could not run" in p.read_text(encoding="utf-8")


def test_main_gate_ado_error_writes_redacted_summary(tmp_path, monkeypatch):
    from ado import __main__ as m
    from ado.client import AdoError
    _gate_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("ADO_TOKEN", "tok-1001-secret")

    def boom(client, args):
        raise AdoError("HTTP 500 echoing tok-1001-secret")
    monkeypatch.setattr(m, "dispatch", boom)
    p = tmp_path / "gate.md"
    assert main(["gate", "1001", "--summary-out", str(p)]) == 1
    md = p.read_text(encoding="utf-8")
    assert "could not run" in md and "tok-1001-secret" not in md


def test_gate_without_token_runs_offline_checks_and_blocks(tmp_path, monkeypatch, capsys):
    from ado import secretstore

    class _EmptyStore:
        def get(self, organization):
            return None
    _gate_repo(tmp_path, monkeypatch, ("ac_traceability",))
    monkeypatch.setattr(secretstore, "resolve_store", lambda: _EmptyStore())
    monkeypatch.setattr("ado.client._OPENER.open", lambda *a, **k: pytest.fail("request sent"))
    assert main(["gate", "1001", "--src", str(tmp_path)]) == 1
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["blocking"] is True and out["blocked_by"] == ["ac_traceability"]
    assert out["drift"]["evaluated"] is False
    assert "error:" not in cap.err


def test_main_gate_summary_write_failure_warns_and_keeps_exit_code(tmp_path, monkeypatch, capsys):
    _gate_repo(tmp_path, monkeypatch, ("ac_traceability",))
    (tmp_path / "blocker").write_text("a file, so mkdir under it raises", encoding="utf-8")
    p = tmp_path / "blocker" / "gate.md"
    assert main(["gate", "1001", "--offline", "--src", str(tmp_path), "--summary-out", str(p)]) == 1
    cap = capsys.readouterr()
    assert json.loads(cap.out)["blocking"] is True
    assert "warning" in cap.err and "summary" in cap.err


def test_main_gate_removed_dod_gate_key_exits_3_with_rename_message(tmp_path, monkeypatch, capsys):
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "dod_gate": {"block": ["assertion"]}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    assert main(["gate", "1001", "--offline"]) == 3
    err = capsys.readouterr().err
    assert "dod_gate" in err and "gate.block" in err


def test_main_gate_bad_test_regex_exits_3_and_still_writes_summary(tmp_path, monkeypatch, capsys):
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "gate": {"tests": {"*.py": "("}}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    p = tmp_path / "gate.md"
    assert main(["gate", "1001", "--offline", "--summary-out", str(p)]) == 3
    assert "Traceback" not in capsys.readouterr().err
    assert "ado.config.json is invalid" in p.read_text(encoding="utf-8")


@pytest.mark.parametrize("argv", [["create", "--type", "Task", "--title", "T"], ["update", "1"]])
def test_system_parent_field_rejected(tmp_path, monkeypatch, capsys, argv):
    (tmp_path / "ado.config.json").write_text(
        json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr("ado.client._OPENER.open",
                        lambda *a, **k: pytest.fail("request sent"))
    assert main(argv + ["--field", "system.parent=3"]) == 2
    assert "ado link <id> --parent <n>" in capsys.readouterr().err


def test_link_parent_dispatch_sets_hierarchy_reverse(cfg):
    class Args:
        command, id, to, url, parent = "link", 7, None, None, 3

    client = FakeClient(cfg)
    dispatch(client, Args())
    assert client.calls[0]["body"][0]["value"]["rel"] == "System.LinkTypes.Hierarchy-Reverse"
    assert client.calls[0]["body"][0]["value"]["url"].endswith("/workItems/3")


# ---- F11: `ado pr create` enforces review_block_on ----
def _review_repo(tmp_path, monkeypatch, blocks="no", blocker=0, code_after=False, ledger=True):
    """Real git repo on branch feat; the ledger's latest review is at the base commit."""
    git(tmp_path, "init", "-q", "-b", "feat")
    sha = commit(tmp_path, "base", {"src/app.py": "x = 1\n", "body.md": "Body\n"})
    if ledger:
        commit(tmp_path, "docs: review log",
               {"dod/DoD_1001.md": ledger_md(review_md(sha, blocks, blocker=blocker))})
    if code_after:
        commit(tmp_path, "unreviewed", {"src/app.py": "x = 2\n"})
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "review_block_on": ["blocker"]}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.setattr("ado.client._OPENER.open", lambda *a, **k: pytest.fail("REST request sent"))
    real_run = subprocess.run

    def run(args, **kw):  # git is allowed (read-only checks); gh never
        assert args[0] == "git", f"unexpected subprocess: {args}"
        return real_run(args, **kw)
    monkeypatch.setattr(subprocess, "run", run)


PR_ARGS = ["pr", "create", "1001", "--title", "T", "--body-file", "body.md", "--base", "main", "--head", "feat"]


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("setup,needle", [
    ({"blocks": "yes", "blocker": 1}, "latest review blocks the PR"),
    ({"blocker": 1}, "1 confirmed `blocker` finding(s)"),
    ({"code_after": True}, "review is stale"),
    ({"ledger": False}, "no review logged"),
])
def test_pr_create_refuses_when_review_blocks(tmp_path, monkeypatch, capsys, git_env, apply, setup, needle):
    _review_repo(tmp_path, monkeypatch, **setup)
    assert main(PR_ARGS + ["--plan-out", "out/plan.md"] + (["--apply"] if apply else [])) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: refusing to open a PR: ") and needle in err
    assert not (tmp_path / "out").exists()


def test_pr_create_passes_when_review_is_fresh_and_clear(tmp_path, monkeypatch, git_env):
    _review_repo(tmp_path, monkeypatch)
    assert main(PR_ARGS + ["--plan-out", "plan.md"]) == 0
    assert (tmp_path / "plan.md").is_file()


def test_pr_comment_ignores_review_block_on(tmp_path, monkeypatch, git_env):
    _review_repo(tmp_path, monkeypatch, "yes", blocker=1)
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "review_block_on": ["blocker"],
         "repo_host": "azure", "repository": "web"}))
    sent = []
    monkeypatch.setattr("ado.__main__.prs.azure_comment", lambda *a: sent.append(a) or {"id": 1})
    assert main(["pr", "comment", "5", "--body-file", "body.md"]) == 0 and sent


# ---- F22: --apply binds to the approved preview (--expect-plan) ----
CHANGED = "plan changed since approval — re-run the preview and approve again"


def _board(monkeypatch, m, kids):
    """ADO stand-in for push: `kids` is the live child list; creates append to it."""
    writes = []

    def create(client, wtype, title, relations=None):
        writes.append(title)
        kids.append({"id": 2000 + len(kids), "title": title, "state": "New"})
        return {"id": kids[-1]["id"]}
    monkeypatch.setattr(m.push.workitems, "children", lambda client, pid: list(kids))
    monkeypatch.setattr(m.push.workitems, "relation", lambda *a: {})
    monkeypatch.setattr(m.push.workitems, "create", create)
    return writes


def test_push_apply_refuses_when_plan_changed(tmp_path, monkeypatch, capsys):
    (tmp_path / "tasks.md").write_text("- [ ] T001 Do it\n")
    m, _ = _push_env(tmp_path, monkeypatch, [])
    writes = _board(monkeypatch, m, [])
    assert m.main(["push", "1001", "--from", "tasks.md", "--plan-out", "p.md"]) == 0
    approved = (tmp_path / "p.md").read_text()
    (tmp_path / "tasks.md").write_text("- [ ] T001 Do it\n- [ ] T002 Edited after approval\n")
    capsys.readouterr()
    assert m.main(["push", "1001", "--from", "tasks.md", "--apply", "--expect-plan", "p.md"]) == 2
    assert CHANGED in capsys.readouterr().err
    assert writes == []
    assert (tmp_path / "p.md").read_text() == approved  # the approved file is never overwritten
    assert "T002" in (tmp_path / "p.md.new").read_text()


def test_push_apply_refuses_when_approved_plan_missing(tmp_path, monkeypatch, capsys):
    (tmp_path / "tasks.md").write_text("- [ ] T001 Do it\n")
    m, _ = _push_env(tmp_path, monkeypatch, [])
    writes = _board(monkeypatch, m, [])
    assert m.main(["push", "1001", "--from", "tasks.md", "--apply", "--expect-plan", "nope.md"]) == 2
    assert CHANGED in capsys.readouterr().err and writes == []
    assert "T001" in (tmp_path / "nope.md.new").read_text()


def test_push_apply_proceeds_when_plan_matches(tmp_path, monkeypatch, capsys):
    (tmp_path / "tasks.md").write_text("- [ ] T001 Do it\n")
    m, _ = _push_env(tmp_path, monkeypatch, [])
    writes = _board(monkeypatch, m, [])
    (tmp_path / "p.md.new").write_text("stale")  # left by an earlier refusal; the preview clears it
    assert m.main(["push", "1001", "--from", "tasks.md", "--plan-out", "p.md"]) == 0
    assert not (tmp_path / "p.md.new").exists()
    assert m.main(["push", "1001", "--from", "tasks.md", "--apply", "--expect-plan", "p.md"]) == 0
    assert writes == ["[sk:T001] Do it"] and not (tmp_path / "p.md.new").exists()


def test_expect_plan_needs_apply(tmp_path, monkeypatch, capsys):
    (tmp_path / "tasks.md").write_text("- [ ] T001 Do it\n")
    m, _ = _push_env(tmp_path, monkeypatch, [])
    _board(monkeypatch, m, [])
    for argv in (["push", "1001", "--from", "tasks.md"], ["done", "1001"],
                 ["pr", "create", "1001", "--from-prepared"], ["questions", "1001", "--post"]):
        with pytest.raises(SystemExit) as e:
            m.main(argv + ["--expect-plan", "p.md"])
        assert e.value.code == 2 and "--expect-plan needs --apply" in capsys.readouterr().err


def test_push_resume_after_partial_failure_refuses_then_reapproves(tmp_path, monkeypatch, capsys):
    """CL-05: apply fails mid-way; re-running the same apply (a workflow resume) must not write a
    plan nobody approved — it refuses, the fresh preview is approved, and only then it applies."""
    from ado.client import AdoError
    (tmp_path / "tasks.md").write_text("- [ ] T001 One\n- [ ] T002 Two\n")
    m, _ = _push_env(tmp_path, monkeypatch, [])
    kids = []
    writes = _board(monkeypatch, m, kids)
    real_create = m.push.workitems.create

    def flaky(client, wtype, title, relations=None):
        if "T002" in title:
            raise AdoError("503 Service Unavailable")
        return real_create(client, wtype, title, relations)
    monkeypatch.setattr(m.push.workitems, "create", flaky)
    apply = ["push", "1001", "--from", "tasks.md", "--apply", "--expect-plan", "p.md"]
    assert m.main(["push", "1001", "--from", "tasks.md", "--plan-out", "p.md"]) == 0
    assert m.main(apply) == 1 and writes == ["[sk:T001] One"]  # partial: T001 created, T002 failed
    (tmp_path / "tasks.md").write_text("- [ ] T001 One\n- [ ] T002 Two, edited after approval\n")
    capsys.readouterr()
    assert m.main(apply) == 2 and writes == ["[sk:T001] One"]  # the resume refuses: no new write
    assert CHANGED in capsys.readouterr().err
    monkeypatch.setattr(m.push.workitems, "create", real_create)
    assert m.main(["push", "1001", "--from", "tasks.md", "--plan-out", "p.md"]) == 0  # re-preview
    plan = (tmp_path / "p.md").read_text()
    assert "unchanged: T001 (1)" in plan and "| create | T002 |" in plan
    assert m.main(apply) == 0  # approved again
    assert writes == ["[sk:T001] One", "[sk:T002] Two, edited after approval"]


def test_dispatch_attachments_lists_and_downloads_to_default_dir(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = {"rel": "AttachedFile", "url": "https://dev.azure.com/contoso/p/_apis/wit/attachments/g",
           "attributes": {"id": 7, "name": "product-note.md", "resourceSize": 4}}

    class Args:
        command, id, download, dir = "attachments", 2, False, None
    assert dispatch(FakeClient(cfg, responses=[{"relations": [rel]}]), Args()) == [
        {"name": "product-note.md", "size": 4, "id": 7}]
    Args.download = True
    out = dispatch(FakeClient(cfg, responses=[{"relations": [rel]}, b"note"]), Args())
    assert (tmp_path / ".ado" / "attachments" / "2" / "product-note.md").read_bytes() == b"note"
    assert out[0]["path"].endswith(os.path.join(".ado", "attachments", "2", "product-note.md"))


def _cfg_dir(tmp_path, monkeypatch):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.setattr("ado.client._OPENER.open", lambda *a, **k: pytest.fail("request sent"))


def test_main_attachments_refused_dir_exits_2_before_request(tmp_path, monkeypatch, capsys):
    _cfg_dir(tmp_path, monkeypatch)
    for bad in ("../out", ".ado", ".ado/context"):
        assert main(["attachments", "2", "--download", "--dir", bad]) == 2
        assert "error: --dir must be inside .ado/attachments/" in capsys.readouterr().err


def test_main_attachments_dir_needs_download(tmp_path, monkeypatch, capsys):
    _cfg_dir(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as e:
        main(["attachments", "2", "--dir", ".ado/x"])
    assert e.value.code == 2 and "--dir needs --download" in capsys.readouterr().err


def test_main_attachments_download_error_exits_1(tmp_path, monkeypatch, capsys):
    from ado import __main__ as m
    _cfg_dir(tmp_path, monkeypatch)
    from ado.client import AdoError
    rel = {"rel": "AttachedFile", "url": "https://dev.azure.com/contoso/p/_apis/wit/attachments/g",
           "attributes": {"id": 7, "name": "a.md", "resourceSize": 4}}
    monkeypatch.setattr(m, "AdoClient", lambda c: FakeClient(c, responses=[{"relations": [rel]}, AdoError("HTTP 404")]))
    assert main(["attachments", "2", "--download"]) == 1
    assert json.loads(capsys.readouterr().out)[0]["error"] == "HTTP 404"


# ---- F29: an invalid config exits 3 for `ado gate` only; a missing config / token stays 2 ----
def _invalid_block_config(tmp_path, monkeypatch):
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "gate": {"block": ["nope"]}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)


def test_gate_invalid_config_exits_3_and_summary_says_failed(tmp_path, monkeypatch, capsys):
    _invalid_block_config(tmp_path, monkeypatch)
    monkeypatch.setenv("ADO_TOKEN", "tok-1001-secret")
    p = tmp_path / "gate.md"
    assert main(["gate", "1001", "--summary-out", str(p)]) == 3
    md = p.read_text(encoding="utf-8")
    assert "ADO gate FAILED: ado.config.json is invalid:" in md and "unknown check (valid: " in md and "nope" not in md
    assert "advisory" not in md and "tok-1001-secret" not in md
    err = capsys.readouterr().err
    assert "unknown check" in err and "nope" not in err


HOSTILE_GLOB = "##vso[task.complete result=Succeeded][z-a]"


@pytest.mark.parametrize("tests", [{HOSTILE_GLOB: "x"}, {"*.py": "x", "ok/**": "(" + "##vso[task.complete result=Succeeded]"}])
def test_gate_bad_gate_tests_exits_3_and_echoes_none_of_the_config_text(tmp_path, monkeypatch, capsys, tests):
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "gate": {"tests": tests}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    p = tmp_path / "gate.md"
    assert main(["gate", "1001", "--summary-out", str(p)]) == 3
    seen = capsys.readouterr()
    for text in (seen.out, seen.err, p.read_text(encoding="utf-8")):
        assert "gate.tests" in text or text == ""
        assert "vso[" not in text and "z-a" not in text and "complete" not in text, text


VSO = "##vso[task.complete result=Succeeded]"


@pytest.mark.parametrize("cfg", [
    {"gate": {"tests": {VSO: "x\n"}, "block": [VSO]}, "review_block_on": [VSO]},   # control character under a hostile glob
    {"gate": {"tests": {VSO: "x"}, "block": ["assertion", VSO]}, "review_block_on": ["major", VSO]},
    {"gate": {"block": [VSO]}}, {"review_block_on": [VSO]}, {"stack": {"phases": {VSO: "x\n"}}},
    {"gate": {"tests": {VSO + "\n": "x"}}},
])
def test_gate_invalid_config_error_paths_never_echo_config_text(tmp_path, monkeypatch, capsys, cfg):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", **cfg}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    p = tmp_path / "gate.md"
    assert main(["gate", "1001", "--summary-out", str(p)]) == 3
    seen = capsys.readouterr()
    for text in (seen.out, seen.err, p.read_text(encoding="utf-8")):
        assert "vso" not in text and "task.complete" not in text, text
    assert seen.err.startswith("error: ")


def test_other_verbs_keep_exit_2_on_invalid_config(tmp_path, monkeypatch):
    _invalid_block_config(tmp_path, monkeypatch)
    monkeypatch.setenv("ADO_TOKEN", "t")
    assert main(["get", "1001"]) == 2


def test_missing_config_stays_exit_2_and_missing_token_is_not_3(tmp_path, monkeypatch):
    from ado import secretstore

    class _EmptyStore:
        def get(self, organization):
            return None
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    assert main(["gate", "1001"]) == 2  # no config: missing information
    _gate_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: _EmptyStore())
    assert main(["get", "1001"]) == 2  # no token
    assert main(["gate", "1001", "--src", str(tmp_path)]) == 0  # gate falls back to offline checks


# ---- P4-12a: comment approval inside the five applies ----
import inspect  # noqa: E402
import shutil  # noqa: E402
import re  # noqa: E402
import types  # noqa: E402

from ado import approvals  # noqa: E402
from ado import __main__ as _main  # noqa: E402
from ado.config import Trust  # noqa: E402
from tests.conftest import RouteClient, raw_comment  # noqa: E402

APPROVER = "55555555-5555-4555-8555-555555555555"
T_APPR = "2026-10-01T10:00:00Z"
_CFG_JSON = json.dumps({"organization": "contoso", "project": "Fabrikam", "repo_host": "azure", "repository": "r"})


def _base_env(tmp_path, monkeypatch):
    (tmp_path / "ado.config.json").write_text(_CFG_JSON)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)


def _v_questions(tmp_path, monkeypatch):
    from tests.test_questions import OPEN2, _ledger
    _base_env(tmp_path, monkeypatch)
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_1001.md").write_text(_ledger(OPEN2), encoding="utf-8")
    return ["questions", "1001", "--post"], lambda seen: [c for c in seen["c"].calls if c["method"] != "GET"], []


def _v_decisions(tmp_path, monkeypatch):
    from tests.test_decisions import OPEN, _file
    _base_env(tmp_path, monkeypatch)
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "Decisions_1001.md").write_text(_file(OPEN), encoding="utf-8")
    return ["decisions", "1001", "--post"], lambda seen: [c for c in seen["c"].calls if c["method"] != "GET"], [{"id": 777}]


def _v_push(tmp_path, monkeypatch):
    _base_env(tmp_path, monkeypatch)
    (tmp_path / "tasks.md").write_text("- [ ] T001 Do it\n")
    writes = _board(monkeypatch, _main, [])
    return ["push", "1001", "--from", "tasks.md"], lambda seen: list(writes), []


def _v_pr(tmp_path, monkeypatch):
    _review_repo(tmp_path, monkeypatch)
    writes = []
    monkeypatch.setattr(_main.prs, "github_create", lambda *a, **k: writes.append(a) or {"url": "https://example.test/pr/1"})
    return PR_ARGS, lambda seen: list(writes), []


def _v_branch(tmp_path, monkeypatch):
    _base_env(tmp_path, monkeypatch)
    monkeypatch.setattr(_main.branch, "require_work_tree", lambda: None)
    monkeypatch.setattr(_main.branch, "link_plan", lambda client, i: {"id": i, "branch": "feature/ab1001-x", "repository": "r", "url": "vstfs:///Git/Ref/p%2Fr%2FGBx", "action": "link"})
    writes = []
    monkeypatch.setattr(_main.workitems, "add_relation", lambda client, i, rel: writes.append((i, rel)))
    return ["branch", "1001", "--link"], lambda seen: list(writes), []


VERBS = {"questions-post": _v_questions, "push": _v_push, "decisions-post": _v_decisions, "pr-create": _v_pr, "branch-link": _v_branch}


@pytest.fixture
def venv(tmp_path, monkeypatch, git_env):
    """`venv(verb)` sets one of the five applies up in comment mode (approver APPROVER, token identity without the bypass bit)."""
    def build(verb, approval="comment", bypass=False, real_trust=False):
        argv, writes, rest = VERBS[verb](tmp_path, monkeypatch)
        if not real_trust:  # the trust read runs git on the target branch; `real_trust` leaves it alone
            monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust([APPROVER], approval, {}))
        env = types.SimpleNamespace(argv=argv, raws=[raw_comment(1, "<p>hello</p>", T_APPR, author="66666666-6666-4666-8666-666666666666")],
                                    seen={}, dir=tmp_path)
        env.writes = lambda: writes(env.seen)

        def make(cfg):
            env.seen["c"] = RouteClient(cfg, env.raws, bypass=bypass, responses=list(rest))
            return env.seen["c"]
        monkeypatch.setattr(_main, "AdoClient", make)
        env.preview = lambda: (_main.main(env.argv + ["--plan-out", "p.md"]), (tmp_path / "p.md").read_bytes())[1]
        env.apply = lambda: _main.main(env.argv + ["--apply", "--expect-plan", "p.md"])
        env.approve = lambda gate, plan, cid=11, text=None: env.raws.append(raw_comment(
            cid, text or f"/approve {gate} {approvals.gate_hash(gate, 1001, plan)}", "2026-10-01T12:00:00Z", author=APPROVER))
        return env
    return build


ALL_GATES = [(v, g) for v, gs in sorted(approvals.VERB_GATES.items()) for g in sorted(gs)]


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_apply_refuses_without_comment_in_comment_mode(venv, capsys, verb):
    env = venv(verb)
    env.preview()
    capsys.readouterr()
    before = {p: p.read_bytes() for p in env.dir.rglob("*.md")}
    assert env.apply() == 2
    err = capsys.readouterr().err
    assert err.startswith("error: comment approval: no valid approval for this plan on story 1001") and "hello" not in err
    assert env.writes() == [] and not (env.dir / "p.md.new").exists()
    assert before == {p: p.read_bytes() for p in env.dir.rglob("*.md")}  # no ledger or decisions file was touched either


@pytest.mark.parametrize("verb,gate", ALL_GATES)
def test_apply_passes_with_any_gate_in_verb_set(venv, capsys, verb, gate):
    env = venv(verb)
    env.approve(gate, env.preview())
    assert env.apply() == 0 and len(env.writes()) == 1


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_gate_outside_verb_set_refused(venv, capsys, verb):
    env = venv(verb)
    plan = env.preview()
    other = next(g for v, g in ALL_GATES if v != verb)
    env.approve(other, plan)
    assert env.apply() == 2 and env.writes() == []


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_require_runs_after_plan_check(venv, capsys, verb):
    env = venv(verb)
    env.preview()
    (env.dir / "p.md").write_text("an older plan\n")
    capsys.readouterr()
    assert env.apply() == 2
    err = capsys.readouterr().err
    assert CHANGED in err and "comment approval" not in err and env.writes() == []
    assert not any("/permissions/" in c["path"] for c in env.seen["c"].calls)  # the plan check refused first: no approval read at all


def test_stale_hash_refused_when_the_plan_changed_after_approval(venv, capsys):
    env = venv("push")
    env.approve("approve-push", env.preview())
    (env.dir / "tasks.md").write_text("- [ ] T001 Do it\n- [ ] T002 Edited after approval\n")
    assert _main.main(env.argv + ["--apply"]) == 2  # no --expect-plan: the hash alone binds the approval to the fresh plan
    assert "no valid approval for this plan" in capsys.readouterr().err and env.writes() == []


def test_a_newer_malformed_line_is_shown_and_refuses(venv, capsys):
    env = venv("push")
    plan = env.preview()
    env.approve("approve-push", plan)
    env.raws.append(raw_comment(12, "/reject approve-push", "2026-10-01T13:00:00Z", author=APPROVER))
    capsys.readouterr()
    assert env.apply() == 2
    assert "1 newer malformed command line(s) by an approver" in capsys.readouterr().err and env.writes() == []


def test_bypass_bit_refuses_the_apply(venv, capsys):
    env = venv("push", bypass=True)
    env.approve("approve-push", env.preview())
    assert env.apply() == 2 and approvals.BYPASS_REFUSAL in capsys.readouterr().err and env.writes() == []


def test_a_plan_that_writes_nothing_needs_no_approval(venv, capsys):
    env = venv("push", )
    (env.dir / "tasks.md").write_text("")
    env.preview()
    assert env.apply() == 0 and env.writes() == []
    assert not any("/permissions/" in c["path"] for c in env.seen["c"].calls)


def test_never_approvable_verbs(tmp_path, monkeypatch, capsys):
    """`done --apply`, `strict override`, any `--force` and the single-item verbs keep today's rules: they never ask for a comment."""
    assert set(approvals.VERB_GATES) == set(VERBS)
    assert set(re.findall(r'_approved\(client, args, "([a-z-]+)"', inspect.getsource(_main))) == set(approvals.VERB_GATES)  # exactly the five call sites
    for fn in (_main._done, _main._override, _main._stack_init, _main._resolve_decision, _main._rules_confirm):
        assert "_approved" not in inspect.getsource(fn) and "approvals.require" not in inspect.getsource(fn), fn.__name__
    _base_env(tmp_path, monkeypatch)
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust([APPROVER], "comment", {}))
    monkeypatch.setattr(approvals, "require", lambda *a, **k: pytest.fail("a comment was asked for"))
    monkeypatch.setattr(_main, "AdoClient", lambda cfg: RouteClient(cfg, [], responses=[{"id": 5, "fields": {}, "rev": 1}] * 4))
    for argv in (["update", "5", "--field", "System.Title=x"], ["create", "--type", "Task", "--title", "x"], ["comment", "5", "hi"], ["link", "5", "--parent", "6"]):
        assert _main.main(argv) in (0, 1, 2), argv


# ---- P4-12a fix: pr create is judged by the stricter of the PR's target branch and origin/HEAD ----
OTHER_APPROVER = "77777777-7777-4777-8777-777777777777"


def _trust_json(approval, approvers=(APPROVER,)):
    d = {"organization": "contoso", "project": "Fabrikam"}
    return json.dumps({**d, "approval": "comment", "approvers": list(approvers)} if approval == "comment" else d)


def _pr_trust_env(tmp_path, monkeypatch, git_env, home, base, base_name="release/x"):
    """A real repository: origin/HEAD -> a default branch whose config is `home`, and a branch `release/x` whose config is `base`. `trusted` is NOT stubbed."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "default", {"ado.config.json": home, "body.md": "Body\n"})
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(tmp_path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git(tmp_path, "switch", "-q", "-c", base_name)
    commit(tmp_path, "release", {"ado.config.json": base, "release.txt": "x"})
    git(tmp_path, "switch", "-q", "main")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setenv("ADO_TOKEN", "t")
    writes = []
    monkeypatch.setattr(_main.prs, "github_create", lambda *a, **k: writes.append(a) or {"url": "https://example.test/pr/1"})
    raws = [raw_comment(1, "<p>hello</p>", T_APPR, author="66666666-6666-4666-8666-666666666666")]
    monkeypatch.setattr(_main, "AdoClient", lambda cfg: RouteClient(cfg, raws))
    return writes, raws


def _pr_apply(tmp_path, raws, base, approve=None):
    argv = ["pr", "create", "1001", "--title", "T", "--body-file", "body.md", "--base", base, "--head", "feat"]
    assert _main.main(argv + ["--plan-out", "p.md"]) == 0
    if approve:
        gate = "approve-pr"
        raws.append(raw_comment(11, f"/approve {gate} {approvals.gate_hash(gate, 1001, (tmp_path / 'p.md').read_bytes())}", "2026-10-01T12:00:00Z", author=approve))
    return _main.main(argv + ["--apply", "--expect-plan", "p.md"])


@pytest.mark.parametrize("home,base,base_name,ok_approver", [
    ("terminal", "comment", "release/x", APPROVER),  # the PR targets a branch in comment mode; the default branch is terminal
    ("comment", "terminal", "release/x", APPROVER),  # the reverse
    ("comment", "comment", "release/x", APPROVER),  # the same approvers on both
])
def test_pr_create_uses_the_stricter_of_base_and_origin_head(tmp_path, monkeypatch, git_env, capsys, home, base, base_name, ok_approver):
    writes, raws = _pr_trust_env(tmp_path, monkeypatch, git_env, _trust_json(home), _trust_json(base))
    assert _pr_apply(tmp_path, raws, "release/x") == 2 and writes == []  # refused without a comment
    assert "comment approval: no valid approval for this plan" in capsys.readouterr().err
    assert _pr_apply(tmp_path, raws, "release/x", approve=ok_approver) == 0 and len(writes) == 1


def test_pr_create_terminal_on_both_needs_no_comment(tmp_path, monkeypatch, git_env, capsys):
    writes, raws = _pr_trust_env(tmp_path, monkeypatch, git_env, _trust_json("terminal"), _trust_json("terminal"))
    assert _pr_apply(tmp_path, raws, "release/x") == 0 and len(writes) == 1


def test_pr_create_refuses_when_the_two_branches_name_different_approvers(tmp_path, monkeypatch, git_env, capsys):
    writes, raws = _pr_trust_env(tmp_path, monkeypatch, git_env, _trust_json("comment"), _trust_json("comment", (OTHER_APPROVER,)))
    assert _pr_apply(tmp_path, raws, "release/x", approve=APPROVER) == 2 and writes == []
    err = capsys.readouterr().err
    assert "different approvers" in err and OTHER_APPROVER not in err and APPROVER not in err


def test_pr_create_with_an_unresolvable_base_uses_origin_head_alone(tmp_path, monkeypatch, git_env, capsys):
    writes, raws = _pr_trust_env(tmp_path, monkeypatch, git_env, _trust_json("terminal"), _trust_json("comment"))
    assert _pr_apply(tmp_path, raws, "nope/x") == 0 and len(writes) == 1  # today's behaviour: the default branch is terminal


def test_pr_create_with_an_unresolvable_base_and_a_comment_default_branch_refuses(tmp_path, monkeypatch, git_env, capsys):
    writes, raws = _pr_trust_env(tmp_path, monkeypatch, git_env, _trust_json("comment"), _trust_json("terminal"))
    assert _pr_apply(tmp_path, raws, "nope/x") == 2 and writes == []
    assert "no valid approval" in capsys.readouterr().err


# ---- P4-12a: terminal mode (no trust keys) is what main e8e4c01 did; the cases that now refuse are named ----
GOLDEN_FILE = Path(__file__).with_name("golden_p4_12a_terminal.json")  # recorded by running this same scenario against main e8e4c01


def _terminal_run(env, capsys):
    env.preview()
    capsys.readouterr()
    rc = env.apply()
    cap = capsys.readouterr()
    norm = lambda t: t.replace(str(env.dir), "<tmp>").replace(os.path.realpath(env.dir), "<tmp>")  # noqa: E731
    return {"rc": rc, "out": norm(cap.out), "err": norm(cap.err), "writes": len(env.writes()),
            "calls": [[c["method"], c["path"], c["body"]] for c in env.seen["c"].calls]}


def _origin_head(repo, files=None):
    """A repository whose origin/HEAD points at a default branch (config without trust keys)."""
    if not (repo / ".git").exists():
        git(repo, "init", "-q", "-b", "main")
        files = {"cfg.txt": "x", **(files or {})}
    if files:  # an existing repository (the PR one) keeps its commits: a new one would make its review stale
        commit(repo, "default", files)
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


@pytest.mark.parametrize("state", ["no-git", "origin-head"])
@pytest.mark.parametrize("verb", sorted(VERBS))
def test_terminal_mode_byte_identical(venv, capsys, verb, state):
    """No trust keys anywhere: the apply prints and sends exactly what main e8e4c01 did (golden values), with `trusted` NOT stubbed."""
    env = venv(verb, real_trust=True)
    if state == "origin-head":
        _origin_head(env.dir)
    got = _terminal_run(env, capsys)
    if os.environ.get("GOLDEN_DUMP"):  # run against main e8e4c01 to record the golden values
        path = Path(os.environ["GOLDEN_DUMP"])
        data = json.loads(path.read_text()) if path.exists() else {}
        data[f"{verb}/{state}"] = got
        path.write_text(json.dumps(data, indent=1, sort_keys=True))
        return
    assert got["rc"] == 0 and got["writes"] == 1
    assert got == json.loads(GOLDEN_FILE.read_text())[f"{verb}/{state}"]


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_a_dangling_origin_head_refuses_the_apply(venv, capsys, verb):
    """Named change (fail closed, as `done` since P4-11): origin/HEAD pointing nowhere cannot be read, so the apply is exit 2 and writes nothing."""
    env = venv(verb, real_trust=True)
    env.preview()
    if not (env.dir / ".git").exists():
        git(env.dir, "init", "-q", "-b", "main")
    git(env.dir, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    capsys.readouterr()
    assert env.apply() == 2 and env.writes() == []
    assert "error:" in capsys.readouterr().err


def test_an_unreadable_target_config_and_working_tree_trust_keys_refuse_the_apply(venv, capsys):
    env = venv("push", real_trust=True)
    env.preview()
    _origin_head(env.dir, {"ado.config.json": "{ not json"})
    capsys.readouterr()
    assert env.apply() == 2 and env.writes() == []  # the target branch's config is unreadable
    env2 = venv("push", real_trust=True)
    (env2.dir / ".git").exists() and shutil.rmtree(env2.dir / ".git")
    (env2.dir / "ado.config.json").write_text(_trust_json("comment"))  # trust keys in the working tree, no origin/HEAD to check them against
    capsys.readouterr()
    assert env2.apply() == 2 and env2.writes() == []
    assert "origin/HEAD or pass --base" in capsys.readouterr().err
