import json

import pytest

from ado import branch
from ado.__main__ import dispatch, main
from ado.config import Config, ConfigError, valid_branch_prefix
from ado.prs import story_from_branch
from tests.conftest import FakeClient, commit, git


@pytest.mark.parametrize("title,expected", [
    ("Count active members", "count-active-members"),
    ("Café: Über-Größe!!", "cafe-uber-groe"),
    ("  --a__b--  ", "a-b"),
    ("x" * 60, "x" * 40),
    ("a" * 39 + " b", "a" * 39),  # cut at 40 never leaves a trailing dash
    ("Count active team members by status and role", "count-active-team-members-by-status-and"),  # no half word
    ("aaaa bbbb " + "c" * 40, "aaaa-bbbb"),  # a word longer than the room is dropped whole
    ("a" * 36 + " bbb ccc", "a" * 36 + "-bbb"),  # a word that ends exactly at 40 is kept
    ("日本語", "story"),
    ("", "story"),
    (None, "story"),
])
def test_slug(title, expected):
    assert branch.slug(title) == expected


@pytest.mark.parametrize("name,prefix,expected", [
    ("feature/ab2-count-active", "feature/", 2),
    ("refs/heads/feature/ab1001-x", "feature/", 1001),
    ("feature/ab12", "feature/", 12),
    ("feature/AB12-x", "feature/", 12),         # `ab` is case-insensitive
    ("ab12-x", "", 12),
    ("bugfix-AB#34-thing", "feature/", 34),
    ("fix/AB#34", "feature/", 34),
    ("AB#7", "", 7),
    ("feature/ab5-x-AB#9", "feature/", 5),      # prefix form wins over AB#
    ("feature/x-AB#9", "feature/", 9),
    ("xAB#9", "feature/", None),                # AB# inside a word
    ("1AB#9", "feature/", None),
    ("feature/ab2147483647-x", "feature/", 2147483647),
    ("feature/ab2147483648-x", "feature/", None),  # over int32
    ("feature/ab" + "9" * 5000, "feature/", None),
    ("AB#" + "9" * 5000, "", None),
    # Spec Kit's own branches never read as a story, whatever the prefix
    ("001-count-active-members", "feature/", None),
    ("001-count-active-members", "", None),
    ("100-x", "", None),
    ("feature/100-x", "feature/", None),
    ("feature/20261028-143022-x", "feature/", None),
    ("20261028-143022-x", "", None),
    ("feature/2-count-active", "feature/", None),  # plain <prefix><n> no longer counts
    ("feature/ab007-x", "feature/", None),
    ("AB#012", "feature/", None),
    ("feature/abc-12", "feature/", None),
    ("feature/ab12x", "feature/", None),
    ("release/1.2", "feature/", None),
    ("main", "feature/", None),
    ("users/feature/ab3-x", "feature/", None),  # prefix only at the start
])
def test_story_from_branch(name, prefix, expected):
    assert story_from_branch(name, prefix) == expected


@pytest.mark.parametrize("prefix,ok", [
    ("", True), ("feature/", True), ("users/team-a/", True), ("story-", True),
    ("/feature", False), ("feature//", False), ("a..b/", False), ("a/.hidden/", False),
    ("x.lock/", False), ("feat ure/", False), ("-x/", False), ("a@{b", False), (7, False),
])
def test_valid_branch_prefix(prefix, ok):
    assert valid_branch_prefix(prefix) is ok


def test_config_branch_prefix_default_and_validation(tmp_path):
    base = {"organization": "contoso", "project": "Fabrikam"}
    (tmp_path / "ado.config.json").write_text(json.dumps(base))
    assert Config.load(tmp_path).branch_prefix == "feature/"
    (tmp_path / "ado.config.json").write_text(json.dumps({**base, "branch_prefix": ""}))
    assert Config.load(tmp_path).branch_prefix == ""
    (tmp_path / "ado.config.json").write_text(json.dumps({**base, "branch_prefix": "bad ref/"}))
    with pytest.raises(ConfigError, match="branch_prefix"):
        Config.load(tmp_path)


# ---- `ado branch` on a real temp git repo ----

@pytest.fixture
def repo(tmp_path, monkeypatch, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "init", {"README.md": "x\n"})
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _story(title="Count active members"):
    return {"id": 2, "fields": {"System.Title": title}}


def _run(cfg, story_id=2, apply=False, title="Count active members"):
    from argparse import Namespace
    client = FakeClient(cfg, responses=[_story(title)])
    return dispatch(client, Namespace(command="branch", id=story_id, apply=apply, id_from=None, link=False))


def _current(repo):
    return git(repo, "symbolic-ref", "--short", "HEAD")


def _feature_json(repo, spec_dir):
    (repo / ".specify").mkdir(exist_ok=True)
    (repo / ".specify" / "feature.json").write_text(json.dumps({"feature_directory": spec_dir}))


def test_create_dry_run_then_apply(repo, cfg):
    assert _run(cfg) == {"branch": "feature/ab2-count-active-members", "action": "create", "applied": False}
    assert _current(repo) == "main"
    out = _run(cfg, apply=True)
    assert out == {"branch": "feature/ab2-count-active-members", "action": "create", "applied": True}
    assert _current(repo) == "feature/ab2-count-active-members"
    assert git(repo, "rev-parse", "main") == git(repo, "rev-parse", "HEAD")  # created from HEAD
    assert _run(cfg) == {"branch": "feature/ab2-count-active-members", "action": "exists", "applied": False}


def test_rename_speckit_branch_created_for_this_spec(repo, cfg):
    git(repo, "switch", "-q", "-c", "001-count-active-members")
    _feature_json(repo, "specs/001-count-active-members")
    out = _run(cfg, apply=True)
    assert out == {"branch": "feature/ab2-count-active-members", "action": "rename",
                   "from": "001-count-active-members", "applied": True}
    assert _current(repo) == "feature/ab2-count-active-members"
    assert "001-count-active-members" not in git(repo, "branch", "--list")


def test_speckit_branch_of_another_spec_is_not_renamed(repo, cfg):
    git(repo, "switch", "-q", "-c", "001-older-feature")
    _feature_json(repo, "specs/002-count-active-members")
    assert _run(cfg, apply=True)["action"] == "create"
    assert "001-older-feature" in git(repo, "branch", "--list")


def test_no_feature_json_means_create_not_rename(repo, cfg):
    git(repo, "switch", "-q", "-c", "001-count-active-members")
    assert _run(cfg)["action"] == "create"


def test_switch_to_existing_story_branch(repo, cfg):
    git(repo, "branch", "feature/ab2-old-title")
    out = _run(cfg, apply=True)
    assert out == {"branch": "feature/ab2-old-title", "action": "switch", "applied": True}
    assert _current(repo) == "feature/ab2-old-title"


def test_current_branch_already_tied_by_ab_ref(repo, cfg):
    git(repo, "switch", "-q", "-c", "fix-AB#2")
    assert _run(cfg, apply=True) == {"branch": "fix-AB#2", "action": "exists", "applied": True}
    assert _current(repo) == "fix-AB#2"


def test_default_branch_is_never_renamed(repo, cfg):
    git(repo, "branch", "-m", "main", "001-count-active-members")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/001-count-active-members")
    _feature_json(repo, "specs/001-count-active-members")
    assert _run(cfg, apply=True)["action"] == "create"
    assert "001-count-active-members" in git(repo, "branch", "--list")


def test_empty_prefix(repo):
    cfg = Config(organization="contoso", project="Fabrikam", branch_prefix="")
    assert _run(cfg)["branch"] == "ab2-count-active-members"


def test_not_a_git_repo_fails_before_any_ado_request(tmp_path, monkeypatch, cfg, git_env):
    from argparse import Namespace
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.chdir(tmp_path)
    client = FakeClient(cfg, responses=[_story()])
    with pytest.raises(ValueError, match="not a git repository"):
        dispatch(client, Namespace(command="branch", id=2, apply=False, id_from=None, link=False))
    assert client.calls == []


def test_round_trip_name_parses_back_to_the_story(repo, cfg):
    for story_id in (1, 2, 1001, 2147483647):
        name = _run(cfg, story_id=story_id)["branch"]
        assert story_from_branch(name, cfg.branch_prefix) == story_id


def test_detached_head_creates_from_head(repo, cfg):
    git(repo, "switch", "-q", "--detach")
    out = _run(cfg, apply=True)
    assert out["action"] == "create" and _current(repo) == "feature/ab2-count-active-members"


def test_switch_blocked_by_local_changes_is_an_error(repo, cfg):
    git(repo, "switch", "-q", "-c", "feature/ab2-x")
    commit(repo, "on story branch", {"README.md": "story\n"})
    git(repo, "switch", "-q", "main")
    (repo / "README.md").write_text("dirty\n")
    with pytest.raises(ValueError, match="git switch feature/ab2-x failed"):
        _run(cfg, apply=True)
    assert _current(repo) == "main" and (repo / "README.md").read_text() == "dirty\n"


def test_speckit_branch_with_upstream_is_not_renamed(repo, cfg):
    git(repo, "switch", "-q", "-c", "001-count-active-members")
    _feature_json(repo, "specs/001-count-active-members")
    git(repo, "config", "branch.001-count-active-members.remote", ".")
    git(repo, "config", "branch.001-count-active-members.merge", "refs/heads/main")
    assert _run(cfg, apply=True)["action"] == "create"
    assert "001-count-active-members" in git(repo, "branch", "--list")


def test_unknown_default_branch_means_create_not_rename(tmp_path, monkeypatch, cfg, git_env):
    git(tmp_path, "init", "-q", "-b", "001-count-active-members")
    commit(tmp_path, "init", {"README.md": "x\n"})
    monkeypatch.chdir(tmp_path)
    _feature_json(tmp_path, "specs/001-count-active-members")
    assert _run(cfg, apply=True)["action"] == "create"
    assert "001-count-active-members" in git(tmp_path, "branch", "--list")


def test_cli_id_from(repo, monkeypatch, capsys):
    (repo / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr("ado.client._OPENER.open", lambda *a, **k: pytest.fail("request sent"))
    assert main(["branch", "--id-from", "refs/heads/feature/ab1001-x"]) == 0
    assert capsys.readouterr().out.strip() == "1001"
    assert main(["branch", "--id-from", "001-count-active-members"]) == 1
    assert capsys.readouterr().out.strip() == "null"


@pytest.mark.parametrize("argv", [
    ["branch"], ["branch", "2", "--id-from", "x"], ["branch", "--id-from", "x", "--apply"], ["branch", "0"],
    ["branch", "2147483648"],
])
def test_cli_branch_usage_errors(argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2
