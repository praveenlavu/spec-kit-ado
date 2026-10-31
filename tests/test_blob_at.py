import json

import pytest

from ado import strict
from ado.config import Config, ConfigInvalid

from tests.conftest import commit, git


@pytest.fixture
def repo(tmp_path, git_env):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "c1", {"ado.config.json": json.dumps({"organization": "contoso", "project": "Fabrikam"}),
                            "sub/dir/x.txt": "x" * 10})
    return tmp_path, git(tmp_path, "rev-parse", "HEAD")


@pytest.mark.parametrize("bad", ["main", "abc1234", "", None, "g" * 40, "a" * 41])
def test_commit_must_be_a_resolved_40_hex_id(repo, bad):
    with pytest.raises(ValueError):
        strict._blob_at(repo[0], bad, "ado.config.json", 100, "L")


def test_reads_a_blob_and_none_when_absent(repo):
    root, sha = repo
    assert strict._blob_at(root, sha, "sub/dir/x.txt", 10, "L") == b"x" * 10
    assert strict._blob_at(root, sha, "nope.txt", 10, "L") is None


def test_cap_text_comes_from_the_cap_and_the_label_is_the_prefix(repo):
    root, sha = repo
    with pytest.raises(ConfigInvalid) as e:
        strict._blob_at(root, sha, "sub/dir/x.txt", 9, "my label")
    assert str(e.value) == "my label: the file is larger than 9 bytes"
    with pytest.raises(ConfigInvalid) as e:
        strict._blob_at(root, sha, "sub", 9, "my label")  # a directory
    assert str(e.value) == "my label: the file is not a regular file"


def test_load_from_ref_records_the_resolved_commit(repo):
    root, sha = repo
    assert Config.load_from_ref("main", repo=root).source_commit == sha
    assert Config(organization="contoso", project="Fabrikam").source_commit is None
