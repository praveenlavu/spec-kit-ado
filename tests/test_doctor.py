import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from ado import __version__, doctor, secretstore
from ado.__main__ import main
from ado.client import ANONYMOUS_ID, AdoError
from ado.config import Config
from tests.conftest import FakeClient, approve, commit, git, profile

TOKEN = "pat-0123456789-SECRET"
ME = "11111111-2222-3333-4444-555555555555"
PID = "99999999-aaaa-bbbb-cccc-dddddddddddd"
PERM_NS, BIT = "52d39943-cb85-4d7f-8fa8-c6baac873819", "1048576"
OTHER = "66666666-7777-8888-9999-000000000000"
NAME, MAIL = "Dana Fictional", "dana@example.invalid"
CFG = {"organization": "contoso", "project": "Fabrikam"}


def me(ident=ME, **over):
    return {"authenticatedUser": {"id": ident, "descriptor": "msa.abc", "providerDisplayName": NAME, "properties": {"Account": {"$value": MAIL}}, **over}}


def tool(bindir, name, out="", code=0, body=None):
    path = Path(bindir) / name
    path.write_text(body or f"#!/bin/sh\nprintf '%s\\n' '{out}'\nexit {code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def env(tmp_path, monkeypatch, git_env):
    """A git repository with a valid config, a PATH holding only real git (a symlink) and a token in the environment."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "ado.config.json").write_text(json.dumps(CFG))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    os.symlink(shutil.which("git"), bindir / "git")
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("ADO_TOKEN", TOKEN)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.chdir(repo)
    return repo, bindir


def rows(result):
    return {r["id"]: r for r in result["rows"]}


def run(repo, responses=None, **kw):
    client = FakeClient(Config(**CFG), responses if responses is not None else [me()])
    return rows(doctor.run(str(repo), client=client, **kw)), client


def test_git_too_old_fails(env):
    repo, bindir = env
    (bindir / "git").unlink()
    tool(bindir, "git", "git version 2.20.1")
    r, _ = run(repo)
    assert r["git"]["status"] == "fail" and "2.20" in r["git"]["fix"] and "2.24" in r["git"]["fix"]
    tool(bindir, "git", "git version 2.24.0")
    assert run(repo)[0]["git"]["status"] == "ok"
    tool(bindir, "git", "something odd")
    assert run(repo)[0]["git"]["status"] == "unknown"
    (bindir / "git").unlink()
    assert run(repo)[0]["git"]["status"] == "fail"


def test_specify_missing_warns(env):
    r, _ = run(env[0])
    assert r["specify"]["status"] == "warn" and "specify" in r["specify"]["fix"]


def test_specify_below_floor_fails(env):
    repo, bindir = env
    ext = repo / ".specify" / "extensions" / "ado"
    ext.mkdir(parents=True)
    (ext / "extension.yml").write_text('schema_version: "1.0"\nextension:\n  id: "ado"\n  version: "0.2.0"\nrequires:\n  speckit_version: ">=0.9.0"\n')
    tool(bindir, "specify", "specify 0.8.9")
    r, _ = run(repo)
    assert r["specify"]["status"] == "fail" and "0.8.9" in r["specify"]["fix"] and "0.9.0" in r["specify"]["fix"]
    tool(bindir, "specify", "specify 1.2.4.dev0")
    assert run(repo)[0]["specify"]["status"] == "ok"
    tool(bindir, "specify", "SECRET-banner without a version")
    r, _ = run(repo)
    assert r["specify"]["status"] == "unknown" and "SECRET" not in r["specify"]["fix"]
    tool(bindir, "specify", "specify 1.2.4", code=1)
    assert run(repo)[0]["specify"]["status"] == "unknown"


def test_specify_floor_default_matches_extension_yml():
    text = (Path(__file__).resolve().parent.parent / "extension.yml").read_text()
    assert f'speckit_version: "{doctor.SPECKIT_FLOOR}"' in text


def test_specify_and_git_run_with_a_scrubbed_env_and_a_timeout(env, monkeypatch):
    repo, bindir = env
    seen = repo.parent / "seen.txt"
    tool(bindir, "specify", body=f"#!/bin/sh\nenv > '{seen}'\necho 'specify 1.2.4'\n")
    monkeypatch.setenv("MY_API_TOKEN", "xyz-secret")
    assert run(repo)[0]["specify"]["status"] == "ok"
    dump = seen.read_text()
    assert TOKEN not in dump and "xyz-secret" not in dump and "ADO_TOKEN" not in dump
    tool(bindir, "specify", body="#!/bin/sh\nsleep 5\n")
    monkeypatch.setattr(doctor, "TIMEOUT", 0.2)
    assert run(repo)[0]["specify"]["status"] == "unknown"


def test_shallow_clone_fails_with_unshallow_fix(env, tmp_path):
    repo, _ = env
    commit(repo, "one", {"a.txt": "1"})
    commit(repo, "two", {"a.txt": "2"})
    assert run(repo)[0]["shallow"]["status"] == "ok"
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo}", str(clone))
    (clone / "ado.config.json").write_text(json.dumps(CFG))
    r, _ = run(clone)
    assert r["shallow"]["status"] == "fail" and "git fetch --unshallow" in r["shallow"]["fix"]


def test_shallow_unknown_outside_a_repository(env, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert run(plain)[0]["shallow"]["status"] == "unknown"


def test_invalid_config_fails_names_key_not_text(env):
    repo, _ = env
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "mode": "SECRETVALUE"}))
    r, client = run(repo)
    assert r["config"]["status"] == "fail" and "'mode'" in r["config"]["fix"] and "SECRETVALUE" not in json.dumps(r)
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "gate": {"block": ["SECRETVALUE"]}}))
    r, _ = run(repo)
    assert "gate.block[0]" in r["config"]["fix"] and "SECRETVALUE" not in json.dumps(r)
    (repo / "ado.config.json").write_text('{"organization": "SECRETVALUE", oops')
    r, _ = run(repo)
    assert r["config"]["status"] == "fail" and "SECRETVALUE" not in json.dumps(r)
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "strict": {"SECRETVALUE": 1}}))
    r, _ = run(repo)
    assert "entry #1" in r["config"]["fix"] and "SECRETVALUE" not in json.dumps(r)
    assert r["stack"]["status"] == "unknown"  # the mode is not known, so a stack problem is not called a warning


def test_missing_config_fails_with_setup_fix(env):
    repo, _ = env
    (repo / "ado.config.json").unlink()
    r = rows(doctor.run(str(repo)))
    assert r["config"]["status"] == "fail" and "ado setup" in r["config"]["fix"]
    assert r["token"]["status"] == "unknown" and r["identity"]["status"] == "unknown"


def _origin(repo, cfg_text):
    """Commit `cfg_text` as the target branch's ado.config.json and point origin/HEAD at it."""
    commit(repo, "target", {"ado.config.json": cfg_text})
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


def test_target_config_unreadable_fails(env):
    repo, _ = env
    assert run(repo)[0]["config-target"]["status"] == "unknown"  # no origin/HEAD
    _origin(repo, "{ not json SECRETVALUE")
    (repo / "ado.config.json").write_text(json.dumps(CFG))  # the working tree is fine; the target is not
    r, _ = run(repo)
    assert r["config"]["status"] == "ok"
    assert r["config-target"]["status"] == "fail" and "SECRETVALUE" not in json.dumps(r)
    _origin(repo, json.dumps({**CFG, "mode": "strict"}))
    r, _ = run(repo)
    assert r["config-target"]["status"] == "ok"
    _origin(repo, json.dumps({**CFG, "branch_prefix": "bad..SECRETVALUE"}))
    r, _ = run(repo)
    assert r["config-target"]["status"] == "fail" and "SECRETVALUE" not in json.dumps(r)


def test_target_without_config_warns(env):
    repo, _ = env
    _origin(repo, "")
    git(repo, "rm", "-q", "--cached", "ado.config.json")
    git(repo, "commit", "-q", "-m", "drop")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert run(repo)[0]["config-target"]["status"] == "warn"


def test_token_absent_fails_never_prints_value(env, monkeypatch):
    repo, _ = env
    monkeypatch.delenv("ADO_TOKEN")
    monkeypatch.setattr(secretstore, "resolve_store", lambda: type("S", (), {"get": lambda self, org: None})())
    r, client = run(repo)
    assert r["token"]["status"] == "fail" and "ADO_TOKEN" in r["token"]["fix"]
    assert r["identity"]["status"] == "unknown" and client.calls == []  # nothing is requested without a token
    monkeypatch.setenv("ADO_TOKEN", TOKEN)
    r, client = run(repo)
    assert r["token"]["status"] == "ok" and r["identity"]["status"] == "ok"
    out = json.dumps(r)
    assert TOKEN not in out and NAME not in out and MAIL not in out and ME not in out


def test_anonymous_connection_data_fails(env):
    repo, _ = env
    for body in (me(ANONYMOUS_ID), me(ANONYMOUS_ID.upper()), me(OTHER, descriptor="System:PublicAccess;aaaaaaaa")):
        r, _ = run(repo, [body])
        assert r["identity"]["status"] == "fail" and r["token"]["status"] == "fail"
        assert "anonymous" in r["identity"]["fix"]


def test_probe_errors_never_say_ok(env):
    repo, _ = env
    cases = [(AdoError("x", 200, "signin"), "fail", "unknown"), (AdoError("x", 401, "signin"), "fail", "unknown"),
             (AdoError("x", 302, "signin"), "fail", "unknown"), (AdoError("x", 403, "http"), "fail", "unknown"),
             (AdoError("x", None, "network"), "unknown", "unknown"), (AdoError("x", None, "http"), "unknown", "unknown"),
             (AdoError("x", 404, "http"), "fail", "unknown"),
             (AdoError("x", 404, "http", "ProjectDoesNotExistWithNameException"), "fail", "unknown"),
             (AdoError("x", 500, "http"), "unknown", "unknown"), ({}, "unknown", "unknown"),
             ({"authenticatedUser": {"descriptor": "msa.x"}}, "unknown", "unknown"), ({"authenticatedUser": {}}, "unknown", "unknown"),
             ({"authenticatedUser": {"id": "  "}}, "unknown", "unknown"), ({"authenticatedUser": {"id": 7}}, "unknown", "unknown"),
             ({"authenticatedUser": {"descriptor": "System:PublicAccess;aaaa"}}, "fail", "fail")]
    for body, token, identity in cases:
        r, _ = run(repo, [body])
        assert (r["token"]["status"], r["identity"]["status"]) == (token, identity), body
    r, _ = run(repo, [{"authenticatedUser": {}}])
    assert "did not say who you are" in r["token"]["fix"] and "did not say who you are" in r["identity"]["fix"]
    r, _ = run(repo, [AdoError("x", 404, "http")])
    assert "check `organization` in ado.config.json" in r["token"]["fix"]


def test_doctor_uses_one_short_attempt(env, monkeypatch):
    repo, _ = env
    made = []
    monkeypatch.setattr("ado.doctor.AdoClient", lambda cfg, **kw: made.append(kw) or FakeClient(cfg, [me()]))
    doctor.run(str(repo))
    assert made == [{"attempts": 1, "timeout": 10}]


def test_crash_paths_name_the_class_never_the_message(env, monkeypatch):
    repo, _ = env
    monkeypatch.setattr("ado.stack.show", lambda *a: (_ for _ in ()).throw(RuntimeError("SECRETVALUE")))
    r, _ = run(repo)
    assert r["stack"]["status"] == "unknown" and "RuntimeError" in r["stack"]["fix"] and "SECRETVALUE" not in json.dumps(r)
    monkeypatch.delenv("ADO_TOKEN")
    monkeypatch.setattr(secretstore, "resolve_store", lambda: type("S", (), {"get": lambda self, org: (_ for _ in ()).throw(KeyError("SECRETVALUE"))})())
    r, client = run(repo)
    # FallbackStore-style failures are the store's business; a raising store is reported by class, and nothing is requested
    assert r["token"]["status"] == "unknown" and "KeyError" in r["token"]["fix"] and "SECRETVALUE" not in json.dumps(r) and client.calls == []
    monkeypatch.setattr("ado.doctor._load_config", lambda repo: (_ for _ in ()).throw(OSError("SECRETVALUE")))
    r, _ = run(repo)
    assert r["config"]["status"] == "unknown" and "OSError" in r["config"]["fix"] and "SECRETVALUE" not in json.dumps(r)


def test_checkout_cannot_lower_the_specify_floor(env):
    repo, bindir = env
    ext = repo / ".specify" / "extensions" / "ado"
    ext.mkdir(parents=True)
    tool(bindir, "specify", "specify 0.1.0")
    (ext / "extension.yml").write_text('extension:\n  version: "0.2.0"\nrequires:\n  speckit_version: ">=0.0.1"\n')
    assert run(repo)[0]["specify"]["status"] == "fail"  # the built-in >=0.9.0 still applies
    tool(bindir, "specify", "specify 0.9.5")
    assert run(repo)[0]["specify"]["status"] == "ok"
    (ext / "extension.yml").write_text('extension:\n  version: "0.2.0"\nrequires:\n  speckit_version: ">=2.0.0"\n')
    assert run(repo)[0]["specify"]["status"] == "fail"  # a checkout can raise it


def test_the_only_requests_are_three_gets(env):
    repo, _ = env
    _, client = run(repo, [me(), {"id": PID}, {"value": [False]}], probe_item=7)
    assert [(c["method"], c["path"].split("?")[0]) for c in client.calls] == [
        ("GET", "/_apis/connectionData"), ("GET", "/_apis/projects/Fabrikam"), ("GET", f"/_apis/permissions/{PERM_NS}/{BIT}")]


def test_workflow_cli_version_mismatch_fails(env):
    repo, _ = env
    wf, ext = repo / ".specify" / "workflows" / "ado-lifecycle", repo / ".specify" / "extensions" / "ado"
    for d in (wf, ext):
        d.mkdir(parents=True)
    (wf / "workflow.yml").write_text('schema_version: "1.0"\nworkflow:\n  id: "ado-lifecycle"\n  version: "9.9.9"\nrequires:\n  speckit_version: ">=0.9.0"\n')
    (ext / "extension.yml").write_text(f'schema_version: "1.0"\nextension:\n  id: "ado"\n  version: "{__version__}"\n')
    r, _ = run(repo)
    assert r["workflow"]["status"] == "fail" and "9.9.9" in r["workflow"]["fix"] and __version__ in r["workflow"]["fix"]
    assert r["extension"]["status"] == "ok"
    (wf / "workflow.yml").write_text(f'workflow:\n  version: "{__version__}"\n')
    assert run(repo)[0]["workflow"]["status"] == "ok"
    (ext / "extension.yml").write_text('extension:\n  version: "0.0.1"\n')
    assert run(repo)[0]["extension"]["status"] == "fail"
    (ext / "extension.yml").write_text("extension:\n  name: x\n")
    assert run(repo)[0]["extension"]["status"] == "unknown"


def test_not_installed_warns_and_symlink_is_not_read(env, tmp_path):
    repo, _ = env
    r, _ = run(repo)
    assert r["extension"]["status"] == "warn" and r["workflow"]["status"] == "warn"
    real = tmp_path / "real.yml"
    real.write_text(f'workflow:\n  version: "{__version__}"\n')
    wf = repo / ".specify" / "workflows" / "ado-lifecycle"
    wf.mkdir(parents=True)
    os.symlink(real, wf / "workflow.yml")
    assert run(repo)[0]["workflow"]["status"] == "unknown"  # never ok through a symlink


def test_stack_and_kits_rows(env):
    repo, _ = env
    r, _ = run(repo)
    assert r["stack"]["status"] == "warn" and r["stack"]["fix"] == "no stack profile yet (optional): run `ado stack detect`"
    assert r["kits"]["status"] == "ok"
    (repo / "ado.stack.json").write_text(json.dumps(approve(profile())))
    assert run(repo)[0]["stack"]["status"] == "ok"
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "mode": "strict", "strict": {"test_command": "PYTHONPATH=src python3 -m pytest -q"}}))
    (repo / "ado.stack.json").write_text(json.dumps(profile()))  # a draft
    r, _ = run(repo)
    assert r["stack"]["status"] == "fail" and "ado stack show" in r["stack"]["fix"]
    (repo / "ado.stack.json").unlink()
    assert run(repo)[0]["stack"]["fix"] == "no stack profile yet: run `ado stack detect`"  # strict: not optional
    (repo / "ado.stack.json").write_text(json.dumps(profile()))
    # an installed kit nobody pinned
    reg = repo / ".specify" / "extensions"
    (reg / "contoso-kit").mkdir(parents=True)
    (reg / "contoso-kit" / "extension.yml").write_text("x")
    (reg / ".registry").write_text(json.dumps({"extensions": {"contoso-kit": {"version": "0.1.0", "enabled": True, "registered_commands": {}}}}))
    r, _ = run(repo)
    assert r["kits"]["status"] == "fail" and "ado kits verify" in r["kits"]["fix"] and "contoso-kit" not in r["kits"]["fix"]
    (repo / "ado.config.json").write_text(json.dumps(CFG))
    assert run(repo)[0]["kits"]["status"] == "warn"
    (reg / ".registry").write_text("{ broken")
    assert run(repo)[0]["kits"]["status"] == "warn"  # a registry that cannot be trusted is a finding, never ok


def test_stack_and_kits_reuse_the_existing_functions(env, monkeypatch):
    repo, _ = env
    calls = []
    monkeypatch.setattr("ado.stack.show", lambda root, mode, cmd: calls.append("stack") or ({"status": "approved", "notes": []}, None, 0))
    monkeypatch.setattr("ado.kits.verify", lambda root, lock: calls.append("kits") or {"kits": [], "findings": []})
    r, _ = run(repo)
    assert calls == ["stack", "kits"] and r["stack"]["status"] == "ok" and r["kits"]["status"] == "ok"


def _target(repo, **keys):
    _origin(repo, json.dumps({**CFG, **keys}))


def test_approver_equal_to_token_identity_fails(env):
    repo, _ = env
    _origin(repo, json.dumps(CFG))
    r, _ = run(repo)
    assert r["approvers"] == {"id": "approvers", "status": "ok", "fix": ""}  # no keys: today's row, byte for byte
    _target(repo, approvers=[OTHER, ME.upper()])
    r, _ = run(repo)
    assert r["approvers"]["status"] == "fail" and ME not in json.dumps(r) and OTHER not in json.dumps(r)
    assert "not an approver" in r["approvers"]["fix"]
    _target(repo, approvers=[OTHER])
    assert run(repo)[0]["approvers"]["status"] == "ok"
    r, _ = run(repo, [AdoError("x", None, "network")])
    assert r["approvers"]["status"] == "unknown" and OTHER not in json.dumps(r)  # the token identity is unknown


def test_approvers_row_prints_the_one_human_reminder_only_when_approvers_are_configured(env):
    repo, _ = env
    _target(repo, approvers=[OTHER])
    r, _ = run(repo)
    fix = r["approvers"]["fix"]
    assert r["approvers"]["status"] == "ok" and "separate account" in fix and "no approver PAT" in fix and "agent" in fix
    assert OTHER not in json.dumps(r) and ME not in json.dumps(r)
    _target(repo)
    assert run(repo)[0]["approvers"]["fix"] == ""


def test_comment_mode_without_approvers_fails(env):
    repo, _ = env
    _target(repo, approval="comment")
    r, _ = run(repo)
    assert r["approvers"]["status"] == "fail" and "nobody can approve" in r["approvers"]["fix"]
    _target(repo, approval="comment", approvers=[])
    assert run(repo)[0]["approvers"]["status"] == "fail"
    _target(repo, approval="comment", approvers=[OTHER])
    assert run(repo)[0]["approvers"]["status"] == "ok"
    _target(repo, approval="terminal")
    assert run(repo)[0]["approvers"]["status"] == "ok"
    # comment mode and an approver that is the token's identity: still the identity failure, and the unknown identity is unknown
    _target(repo, approval="comment", approvers=[ME])
    assert run(repo)[0]["approvers"]["status"] == "fail"
    r, _ = run(repo, [AdoError("x", None, "network")])
    assert r["approvers"]["status"] == "unknown"
    _target(repo, approval="comment")
    assert run(repo, [AdoError("x", None, "network")])[0]["approvers"]["status"] == "fail"  # needs no identity


def test_working_tree_trust_keys_change_nothing_in_the_approvers_row(env):
    repo, _ = env
    _origin(repo, json.dumps(CFG))
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "approvers": [ME], "approval": "comment"}))  # the PR's edit
    r, _ = run(repo)
    assert r["approvers"] == {"id": "approvers", "status": "ok", "fix": ""}


def test_approvers_refusal_names_every_file_that_can_be_unreadable(env, monkeypatch, tmp_path):
    """F7 (F6 N3): the generic refusal is true whichever file `config.trusted` could not read: the target's, the working tree's or ADO_CONFIG's."""
    repo, _ = env
    _origin(repo, json.dumps(CFG))
    junk = tmp_path / "junk.json"
    junk.write_text("{ not json SECRETVALUE")
    monkeypatch.setenv("ADO_CONFIG", str(junk))
    fix = run(repo)[0]["approvers"]["fix"]
    assert "ADO_CONFIG" in fix and "working-tree" in fix and "target branch" in fix and "config-target" in fix and "SECRETVALUE" not in fix


def test_approvers_unknown_when_target_unreadable_unless_none_configured(env):
    repo, _ = env
    assert run(repo)[0]["approvers"]["status"] == "ok"  # no origin/HEAD, and the working tree has no approvers key
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "approvers": [ME]}))
    r, _ = run(repo)
    assert r["approvers"]["status"] == "fail"  # a working-tree key counts for nothing: only the target branch is read, and the verbs refuse
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "approval": "comment"}))
    assert run(repo)[0]["approvers"]["status"] == "fail"


def test_json_shape(env, capsys, monkeypatch):
    repo, _ = env
    result = doctor.run(str(repo), client=FakeClient(Config(**CFG), [me()]))
    assert list(result) == ["rows", "scopes"] and result["scopes"] == {}
    assert [r["id"] for r in result["rows"]] == ["git", "specify", "shallow", "config", "config-target", "token", "identity",
                                                  "extension", "workflow", "approvers", "bypass", "stack", "kits"]
    assert all(list(r) == ["id", "status", "fix"] and r["status"] in ("ok", "warn", "fail", "unknown") and isinstance(r["fix"], str) for r in result["rows"])
    monkeypatch.setattr("ado.doctor.AdoClient", lambda cfg, **kw: FakeClient(cfg, [me()]))
    assert main(["doctor", "--json"]) in (0, 1)
    out = json.loads(capsys.readouterr().out)
    assert list(out) == ["rows", "scopes"] and [r["id"] for r in out["rows"]] == [r["id"] for r in result["rows"]]


def test_cli_exit_code_and_text_output(env, capsys, monkeypatch):
    repo, bindir = env
    monkeypatch.setattr("ado.doctor.AdoClient", lambda cfg, **kw: FakeClient(cfg, [me()]))
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "mode": "nope"}))
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "fail" in out and "config" in out and not out.lstrip().startswith("{")
    (repo / "ado.config.json").write_text(json.dumps(CFG))
    monkeypatch.setattr("ado.doctor.AdoClient", lambda cfg, **kw: FakeClient(cfg, [me()]))
    assert main(["doctor", "--probe-item", "5"]) == 0  # only warn/unknown/ok rows
    assert TOKEN not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["doctor", "--probe-item", "0"])


def test_doctor_writes_nothing(env):
    repo, _ = env

    def snapshot():
        return sorted((str(p), p.stat().st_mtime_ns) for p in repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo).parts)
    before = snapshot()
    run(repo)
    assert snapshot() == before and git(repo, "status", "--porcelain") == "?? ado.config.json"


# ---- F6 (P4-20 slice B): the bypass row, GUID identity, the trusted() path in the approvers row ----

def bypass_run(repo, bit, ident=None):
    """doctor with connectionData, the project (its id) and the permission answer queued; returns (rows, client)."""
    return run(repo, [ident or me(), {"id": PID}, {"value": [bit]}])


def test_bypass_row_ok_when_the_bit_is_clear(env):
    repo, _ = env
    r, client = bypass_run(repo, False)
    assert r["bypass"] == {"id": "bypass", "status": "ok", "fix": ""}
    assert [c["method"] for c in client.calls] == ["GET", "GET", "GET"]  # reads only: doctor never writes
    assert PID in client.calls[2]["path"].replace("%3A", ":").replace("%2F", "/")  # the project token, as approvals.bypass_bit builds it


def test_bypass_row_fails_when_set_and_the_target_uses_comment_mode_or_approvers(env):
    repo, _ = env
    _target(repo, approval="comment", approvers=[OTHER])
    r, _ = bypass_run(repo, True)
    assert r["bypass"]["status"] == "fail" and "Bypass rules" in r["bypass"]["fix"] and "Contributor" in r["bypass"]["fix"]
    _target(repo, approvers=[OTHER])
    assert bypass_run(repo, True)[0]["bypass"]["status"] == "fail"
    _target(repo, approval="comment")
    assert bypass_run(repo, True)[0]["bypass"]["status"] == "fail"


def test_bypass_row_warns_when_set_and_no_trust_keys(env):
    repo, _ = env
    _target(repo)
    r, _ = bypass_run(repo, True)
    assert r["bypass"]["status"] == "warn" and "Contributor" in r["bypass"]["fix"]
    _target(repo, approval="terminal")
    assert bypass_run(repo, True)[0]["bypass"]["status"] == "warn"


def test_bypass_row_set_with_an_unreadable_trust_target_fails_closed(env):
    repo, _ = env  # no origin/HEAD and trust keys in the working tree: trusted() refuses, so comment mode could be in force
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "approval": "comment"}))
    assert bypass_run(repo, True)[0]["bypass"]["status"] == "fail"


@pytest.mark.parametrize("answers", [
    [me(), AdoError("x", 403, "http")],                          # project not readable
    [me(), {"id": "not-a-guid"}],                                  # project id is no GUID
    [me(), {"id": PID}, AdoError("x", 401, "http")],               # a restricted PAT may not read the permission
    [me(), {"id": PID}, {"value": []}], [me(), {"id": PID}, {"value": [1]}], [me(), {"id": PID}, {"value": [True, False]}], [me(), {"id": PID}, []]])
def test_bypass_row_unknown_when_it_cannot_be_read_never_ok(env, answers):
    repo, _ = env
    r, client = run(repo, answers)
    assert r["bypass"]["status"] == "unknown" and "comment mode refuses" in r["bypass"]["fix"]
    assert {c["method"] for c in client.calls} == {"GET"}


def test_bypass_row_unknown_and_unrequested_when_the_token_is_not_accepted(env):
    repo, _ = env
    r, client = run(repo, [AdoError("x", 401, "http")])
    assert r["bypass"]["status"] == "unknown" and len(client.calls) == 1  # only connectionData was sent
    r, client = run(repo, [me(ANONYMOUS_ID)])
    assert r["bypass"]["status"] == "unknown" and len(client.calls) == 1


def test_bypass_row_texts_are_fixed(env):
    repo, _ = env
    _target(repo, approvers=[OTHER])
    out = json.dumps(bypass_run(repo, True)[0]) + json.dumps(run(repo, [me(), {"id": PID}, AdoError("x", 500, "http", "SECRETVALUE")])[0])
    assert PID not in out and ME not in out and OTHER not in out and "SECRETVALUE" not in out and NAME not in out


def test_bypass_row_a_crash_is_reported_by_class(env, monkeypatch):
    repo, _ = env
    monkeypatch.setattr("ado.approvals.bypass_bit", lambda client: (_ for _ in ()).throw(RuntimeError("SECRETVALUE")))
    r, _ = run(repo)
    assert r["bypass"]["status"] == "unknown" and "RuntimeError" in r["bypass"]["fix"] and "SECRETVALUE" not in json.dumps(r)


def test_doctor_with_bypass_still_builds_one_short_client(env, monkeypatch):
    repo, _ = env
    made = []
    monkeypatch.setattr("ado.doctor.AdoClient", lambda cfg, **kw: made.append(kw) or FakeClient(cfg, [me(), {"id": PID}, {"value": [False]}]))
    assert rows(doctor.run(str(repo)))["bypass"]["status"] == "ok"
    assert made == [{"attempts": 1, "timeout": 10}]


@pytest.mark.parametrize("ident", ["{" + ME + "}", "not-a-guid", " " + ME, ME + " ", ME[:-1]])
def test_identity_must_be_a_guid(env, ident):
    repo, _ = env
    _target(repo, approvers=[OTHER])
    r, client = run(repo, [me(ident)])
    assert r["identity"]["status"] == "unknown" and "not an identity id" in r["identity"]["fix"]
    assert r["token"]["status"] == "ok" and r["approvers"]["status"] == "unknown"  # never ok on an id the verbs would refuse
    assert ident.strip() not in json.dumps(r)
    assert r["bypass"]["status"] == "unknown"


def test_a_plain_guid_identity_is_still_ok_in_upper_case(env):
    repo, _ = env
    r, _ = run(repo, [me(ME.upper())])
    assert r["identity"]["status"] == "ok"


NONROOT = "the config file in use is not the repo-root ado.config.json"


def test_approvers_row_reports_the_trusted_refusal_in_fixed_text(env):
    repo, _ = env
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "approvers": [OTHER]}))  # no origin/HEAD
    r, _ = run(repo)
    fix = r["approvers"]["fix"]
    assert r["approvers"]["status"] == "fail" and "origin/HEAD" in fix and OTHER not in json.dumps(r)
    (repo / "ado.config.json").write_text(json.dumps({**CFG, "unattended": {"assume_after_hours": 5}}))  # `unattended` counts too
    assert run(repo)[0]["approvers"]["status"] == "fail"
    (repo / "ado.config.json").write_text(json.dumps(CFG))
    assert run(repo)[0]["approvers"] == {"id": "approvers", "status": "ok", "fix": ""}  # a fresh repo with no trust keys is unchanged


def test_approvers_row_reports_the_non_root_config_refusal(env, monkeypatch, tmp_path):
    repo, _ = env
    _origin(repo, json.dumps(CFG))
    other = tmp_path / "elsewhere.json"
    other.write_text(json.dumps({**CFG, "approvers": [OTHER]}))
    monkeypatch.setenv("ADO_CONFIG", str(other))
    r, _ = run(repo)
    assert r["approvers"]["status"] == "fail" and NONROOT in r["approvers"]["fix"] and OTHER not in json.dumps(r)


def test_approvers_row_target_unreadable_is_a_fixed_refusal_text(env):
    repo, _ = env
    _origin(repo, "{ not json SECRETVALUE")
    (repo / "ado.config.json").write_text(json.dumps(CFG))
    r, _ = run(repo)
    assert r["approvers"]["status"] == "fail" and "SECRETVALUE" not in json.dumps(r) and "config-target" in r["approvers"]["fix"]
