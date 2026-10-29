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
