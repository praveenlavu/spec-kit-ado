import json
from argparse import Namespace

import pytest

from ado import branch
from ado.__main__ import dispatch, main
from ado.config import Config
from tests.conftest import FakeClient, commit, git

PID, RID = "0a1b2c3d-0000-4000-8000-00000000abcd", "9F8E7D6C-0000-4000-8000-00000000ABCD"
BRANCH = "feature/ab2-count-active-members"
URL = f"vstfs:///Git/Ref/{PID}%2F{RID}%2FGBfeature%2Fab2-count-active-members"
AZ = Config(organization="contoso", project="Fabrikam Space", repo_host="azure", repository="Fabrikam Repo")


@pytest.mark.parametrize("name,tail", [
    ("feature/ab2-x", "feature%2Fab2-x"),
    ("feature/ab2 x", "feature%2Fab2%20x"),
    ("feature/ab2-café", "feature%2Fab2-caf%C3%A9"),
    ("fix-AB#2", "fix-AB%232"),
])
def test_artifact_url_encodes_the_branch(name, tail):
    assert branch.artifact_url(PID, RID, name) == f"vstfs:///Git/Ref/{PID}%2F{RID}%2FGB{tail}"


def test_artifact_url_keeps_guid_case_as_returned():
    assert branch.artifact_url("AbC", "dEf", "x").startswith("vstfs:///Git/Ref/AbC%2FdEf%2FGBx")


@pytest.fixture
def repo(tmp_path, monkeypatch, git_env):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    commit(work, "init", {"README.md": "x\n"})
    git(work, "remote", "add", "origin", str(remote))
    git(work, "switch", "-q", "-c", BRANCH)
    monkeypatch.chdir(work)
    return work


def _push(repo):
    git(repo, "push", "-q", "origin", BRANCH)


def _client(cfg=AZ, relations=()):
    return FakeClient(cfg, responses=[{"id": PID}, {"id": RID}, {"id": 2, "relations": list(relations)}])


def _args(**kw):
    return Namespace(**{"command": "branch", "id": 2, "apply": False, "id_from": None, "link": True,
                        "plan_out": None, "expect_plan": None, **kw})


def test_dry_run_plans_the_link_and_writes_nothing(repo, tmp_path):
    _push(repo)
    client, plan = _client(), tmp_path / "plan.md"
    out = dispatch(client, _args(plan_out=str(plan)))
    assert out == {"id": 2, "branch": BRANCH, "repository": "Fabrikam Repo", "url": URL,
                   "action": "link", "applied": False}
    assert [(c["method"], c["path"]) for c in client.calls] == [
        ("GET", "/_apis/projects/Fabrikam%20Space?api-version=7.1"),
        ("GET", "/Fabrikam%20Space/_apis/git/repositories/Fabrikam%20Repo?api-version=7.1"),
        ("GET", "/Fabrikam%20Space/_apis/wit/workitems/2?$expand=relations&api-version=7.1")]
    assert URL in plan.read_text()


def test_apply_payload(repo, tmp_path):
    _push(repo)
    client, plan = _client(), tmp_path / "plan.md"
    dispatch(_client(), _args(plan_out=str(plan)))
    out = dispatch(client, _args(apply=True, expect_plan=str(plan)))
    assert out["applied"] is True
    patch = client.calls[-1]
    assert patch["method"] == "PATCH" and patch["path"] == "/Fabrikam%20Space/_apis/wit/workitems/2?api-version=7.1"
    assert patch["content_type"] == "application/json-patch+json"
    assert patch["body"] == [{"op": "add", "path": "/relations/-",
                              "value": {"rel": "ArtifactLink", "url": URL, "attributes": {"name": "Branch"}}}]


@pytest.mark.parametrize("existing", [URL, URL.replace(PID, PID.upper()).replace(RID, RID.lower()),
                                      URL.replace("%2F", "%2f")])
def test_already_linked_is_a_no_op(repo, existing):
    _push(repo)
    client = _client(relations=[{"rel": "ArtifactLink", "url": existing, "attributes": {"name": "Branch"}}])
    out = dispatch(client, _args(apply=True))
    assert out["action"] == "exists" and out["applied"] is False
    assert [c["method"] for c in client.calls] == ["GET", "GET", "GET"]
    assert "Already linked" in branch.render_link_plan(out)


def test_same_url_under_another_rel_is_not_linked(repo):
    _push(repo)
    assert dispatch(_client(relations=[{"rel": "Hyperlink", "url": URL}]), _args())["action"] == "link"


def test_branch_name_case_is_significant(repo):
    _push(repo)
    other = URL.replace("feature%2Fab2", "Feature%2Fab2")
    assert dispatch(_client(relations=[{"rel": "ArtifactLink", "url": other}]), _args())["action"] == "link"


def test_expect_plan_mismatch_exits_2_and_writes_nothing(repo, tmp_path, monkeypatch, capsys):
    _push(repo)
    (repo / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "repo_host": "azure", "repository": "r"}))
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setenv("ADO_TOKEN", "t")
    stale = tmp_path / "plan.md"
    stale.write_text("an older plan\n")
    sent = []
    resp = iter([{"id": PID}, {"id": RID}, {"id": 2}])
    monkeypatch.setattr("ado.client.AdoClient.request",
                        lambda self, method, path, *a, **k: sent.append(method) or next(resp))
    assert main(["branch", "2", "--link", "--apply", "--expect-plan", str(stale)]) == 2
    assert "plan changed since approval" in capsys.readouterr().err
    assert sent == ["GET", "GET", "GET"] and (tmp_path / "plan.md.new").exists()


def test_github_is_refused_before_anything_else(repo):
    client = _client(Config(organization="contoso", project="Fabrikam"))
    with pytest.raises(ValueError, match="Azure Repos only.*AB#"):
        dispatch(client, _args())
    assert client.calls == []


def test_branch_mismatch_is_refused(repo):
    git(repo, "switch", "-q", "-c", "feature/ab3-other")
    client = _client()
    with pytest.raises(ValueError, match="not tied to story 2"):
        dispatch(client, _args())
    assert client.calls == []


def test_not_pushed_is_refused(repo):
    client = _client()
    with pytest.raises(ValueError, match="push the branch first"):
        dispatch(client, _args())
    assert client.calls == []


def test_other_remote_branch_does_not_count_as_pushed(repo):
    git(repo, "push", "-q", "origin", f"{BRANCH}:refs/heads/x/{BRANCH}")
    with pytest.raises(ValueError, match="push the branch first"):
        dispatch(_client(), _args())


@pytest.mark.parametrize("argv", [
    ["branch", "2", "--plan-out", "p.md"], ["branch", "2", "--apply", "--expect-plan", "p.md"],
    ["branch", "--id-from", "x", "--link"],
])
def test_cli_usage_errors(argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2



def test_no_origin_is_a_clean_exit_2_with_the_fatal_line(repo, monkeypatch, capsys):
    git(repo, "remote", "remove", "origin")
    (repo / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "repo_host": "azure", "repository": "r"}))
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setenv("ADO_TOKEN", "t")
    assert main(["branch", "2", "--link"]) == 2
    assert "error: cannot read remote `origin`: fatal: " in capsys.readouterr().err


def test_remote_url_userinfo_is_never_echoed(repo):
    git(repo, "remote", "set-url", "origin", "https://alice:s3cret@127.0.0.1:1/x.git")
    with pytest.raises(ValueError, match="cannot read remote") as e:
        dispatch(_client(), _args())
    assert "alice" not in str(e.value) and "s3cret" not in str(e.value)


def test_userinfo_is_stripped_from_the_fatal_line(repo, monkeypatch):
    err = "warning: x\nfatal: unable to access 'https://alice:s3cret@host/x.git/': nope\nfatal: second\n"
    monkeypatch.setattr(branch.subprocess, "run", lambda *a, **k: branch.subprocess.CompletedProcess(a, 128, "", err))
    with pytest.raises(ValueError) as e:
        branch._ls_remote("refs/heads/x")
    assert str(e.value) == "cannot read remote `origin`: fatal: unable to access 'https://host/x.git/': nope"


def test_ls_remote_never_prompts_and_times_out_cleanly(repo, monkeypatch):
    seen = {}

    def boom(*a, **k):
        seen.update(k)
        raise branch.subprocess.TimeoutExpired(a, k["timeout"])
    monkeypatch.setattr(branch.subprocess, "run", boom)
    with pytest.raises(ValueError, match="timed out after 30 s"):
        branch._ls_remote("refs/heads/x")
    assert seen["timeout"] == 30 and seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
