import json
from pathlib import Path

import pytest

from ado.config import REVIEW_SEVERITIES, Config, ConfigError
from ado.dod import SECTION2, ac_hash
from ado.gate import GATE_CHECKS, run_gate


def test_load_from_cwd_file(tmp_path):
    (tmp_path / "ado.config.json").write_text(
        json.dumps({"organization": "contoso", "project": "Fabrikam"})
    )
    cfg = Config.load(start_dir=tmp_path)
    assert cfg.organization == "contoso"
    assert cfg.project == "Fabrikam"
    assert cfg.api_version == "7.1"


def test_env_var_path_wins(tmp_path, monkeypatch):
    env_file = tmp_path / "other.json"
    env_file.write_text(json.dumps({"organization": "envorg", "project": "P2"}))
    (tmp_path / "ado.config.json").write_text(
        json.dumps({"organization": "cwdorg", "project": "P1"})
    )
    monkeypatch.setenv("ADO_CONFIG", str(env_file))
    assert Config.load(start_dir=tmp_path).organization == "envorg"


def test_missing_required_key_raises(tmp_path):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "x"}))
    with pytest.raises(ConfigError, match="project"):
        Config.load(start_dir=tmp_path)


def test_no_config_anywhere_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    with pytest.raises(ConfigError, match="ado setup"):
        Config.load(start_dir=tmp_path)


def test_malformed_json_raises_config_error(tmp_path):
    (tmp_path / "ado.config.json").write_text("{not valid json")
    with pytest.raises(ConfigError, match="invalid JSON"):
        Config.load(start_dir=tmp_path)


def test_dod_path_defaults_to_dod(tmp_path):
    (tmp_path / "ado.config.json").write_text(
        json.dumps({"organization": "contoso", "project": "P"})
    )
    assert Config.load(start_dir=tmp_path).dod_path == "dod"


def test_dod_path_from_config(tmp_path):
    (tmp_path / "ado.config.json").write_text(
        json.dumps({"organization": "contoso", "project": "P", "dod_path": "docs/dod"})
    )
    assert Config.load(start_dir=tmp_path).dod_path == "docs/dod"


def _write(tmp_path, **extra):
    (tmp_path / "ado.config.json").write_text(
        json.dumps({"organization": "o", "project": "p", **extra}))
    return Config.load(start_dir=tmp_path)


def test_defaults_for_new_keys(tmp_path):
    cfg = _write(tmp_path)
    assert cfg.gate == {"tests": {}, "block": []}
    assert cfg.attachments_allow == [".md", ".svg", ".txt"]
    assert cfg.review_block_on == []
    assert cfg.done_state == "Done"
    assert cfg.task_type == "Task"
    assert cfg.repo_host == "github"
    assert cfg.repository == ""


def test_custom_states_and_task_type(tmp_path):
    cfg = _write(tmp_path, done_state="Closed", task_type="Issue")
    assert (cfg.done_state, cfg.task_type) == ("Closed", "Issue")


def test_partial_gate_block_only_gets_tests_default(tmp_path):
    assert _write(tmp_path, gate={"block": ["assertion"]}).gate == {"tests": {}, "block": ["assertion"]}


def test_partial_gate_tests_only_gets_block_default(tmp_path):
    cfg = _write(tmp_path, gate={"tests": {"apex": "y"}})
    assert cfg.gate == {"tests": {"apex": "y"}, "block": []}


def test_string_attachments_allow_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="attachments_allow"):
        _write(tmp_path, attachments_allow=".md")


def test_non_string_entry_in_attachments_allow_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="attachments_allow"):
        _write(tmp_path, attachments_allow=[".md", 1])


@pytest.mark.parametrize("key", ["gate", "stack"])
def test_non_object_gate_stack_is_config_error(tmp_path, key):
    with pytest.raises(ConfigError, match=key):
        _write(tmp_path, **{key: None})


def test_top_level_non_object_is_config_error(tmp_path):
    (tmp_path / "ado.config.json").write_text("[]")
    with pytest.raises(ConfigError, match="ado.config.json.*object"):
        Config.load(start_dir=tmp_path)


@pytest.mark.parametrize("org", ["../x", ".hidden", "a/b", "a b", "a?b", 5, ["x"]])
def test_bad_organization_is_config_error(tmp_path, org):
    with pytest.raises(ConfigError, match="organization"):
        _write(tmp_path, organization=org)


@pytest.mark.parametrize("org", ["contoso", "Fab-rikam_1", "a.b", "_x"])
def test_valid_organization_accepted(tmp_path, org):
    assert _write(tmp_path, organization=org).organization == org


def test_gate_tests_not_object_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="gate.tests"):
        _write(tmp_path, gate={"tests": []})


@pytest.mark.parametrize("block", ["assertion", [1], None, {"a": 1}])
def test_gate_block_not_list_of_strings_is_config_error(tmp_path, block):
    with pytest.raises(ConfigError, match="gate.block"):
        _write(tmp_path, gate={"block": block})


def test_utf8_project_round_trips_through_setup(monkeypatch, tmp_path):
    from ado import setup_cmd, secretstore
    from tests.conftest import FakeClient
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    monkeypatch.setattr(setup_cmd, "AdoClient",
                        lambda cfg: FakeClient(cfg, responses=[{"name": "Équipe"}]))
    it = iter(["contoso", "Équipe"])
    out = tmp_path / "ado.config.json"
    assert setup_cmd.run_setup(input_fn=lambda _p: next(it),
                               getpass_fn=lambda _p: "pat", out_path=out) == 0
    assert "Équipe" in out.read_bytes().decode("utf-8")  # literal UTF-8 on disk
    assert Config.load(start_dir=tmp_path).project == "Équipe"


def test_main_maps_invalid_config_to_error_exit_2(tmp_path, monkeypatch, capsys):
    from ado.__main__ import main
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "../x", "project": "p"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    assert main(["get", "1"]) == 2
    assert "error:" in capsys.readouterr().err


def test_organization_trailing_newline_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="organization"):
        _write(tmp_path, organization="contoso\n")


def test_unreadable_config_is_config_error_naming_file(tmp_path, monkeypatch):
    f = tmp_path / "ado.config.json"
    f.write_text(json.dumps({"organization": "o", "project": "p"}))
    monkeypatch.delenv("ADO_CONFIG", raising=False)

    def deny(self, *a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(Path, "read_text", deny)
    with pytest.raises(ConfigError, match="ado.config.json"):
        Config.load(start_dir=tmp_path)


def test_removed_dod_gate_key_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="dod_gate.*renamed.*gate.block"):
        _write(tmp_path, dod_gate={"block": ["assertion"]})


@pytest.mark.parametrize("tests,entry", [
    ({"*.py": 5}, "#1"), ({"*.py": "("}, "#1"), ({"": "x"}, "#1"), ({"*.py": "x", "a.py": ""}, "#2"),
    ({"*.py": "x" * 501}, "#1"),                     # regex over the 500 cap
    ({"x" * 201: "x"}, "#1"),                        # glob over the 200 cap
    ({"src/[": "x"}, "#1"), ({"[!]": "x"}, "#1"),    # unbalanced class: the matcher would not take it
    ({"[z-a]": "x"}, "#1"),                          # a class the regex engine rejects
])
def test_bad_gate_tests_entry_is_config_error_naming_the_entry_not_the_text(tmp_path, tests, entry):
    with pytest.raises(ConfigInvalid, match="gate.tests' entry " + entry) as e:
        _write(tmp_path, gate={"tests": tests})
    assert not any(k and k in str(e.value) for k in tests if len(k) < 100) and "xxxx" not in str(e.value)


def test_gate_tests_at_the_caps_and_valid_classes_load(tmp_path):
    tests = {"x" * 200: "y" * 500, "**/*.test.[jt]s": "expect", "a[xy]b": "z"}
    assert _write(tmp_path, gate={"tests": tests}).gate["tests"] == tests


@pytest.mark.parametrize("p", [5, "", None, ["d"]])
def test_bad_dod_path_is_config_error(tmp_path, p):
    with pytest.raises(ConfigError, match="dod_path"):
        _write(tmp_path, dod_path=p)


def test_story_type_and_removed_state(tmp_path):
    cfg = _write(tmp_path)
    assert (cfg.story_type, cfg.removed_state) == ("User Story", "Removed")
    cfg = _write(tmp_path, story_type="Product Backlog Item", removed_state="Cut")
    assert (cfg.story_type, cfg.removed_state) == ("Product Backlog Item", "Cut")


@pytest.mark.parametrize("key", ["story_type", "removed_state", "done_state"])
@pytest.mark.parametrize("bad", ["", 3, None])
def test_story_type_removed_state_must_be_nonempty_string(tmp_path, key, bad):
    with pytest.raises(ConfigError, match=key):
        _write(tmp_path, **{key: bad})


def test_review_block_on_loaded(tmp_path):
    assert _write(tmp_path, review_block_on=["blocker", "major"]).review_block_on == ["blocker", "major"]


@pytest.mark.parametrize("bad", ["blocker", ["blocker", 1], {"a": 1}, None])
def test_review_block_on_must_be_list_of_strings(tmp_path, bad):
    with pytest.raises(ConfigError, match="review_block_on"):
        _write(tmp_path, review_block_on=bad)


@pytest.mark.parametrize("name", ["ac_traceability", "assertion", "ac_drift", "open_questions",
                                  "commits_without_story", "open_decisions"])
def test_known_gate_block_name_accepted(tmp_path, name):
    assert _write(tmp_path, gate={"block": [name]}).gate["block"] == [name]


def test_config_accepts_open_decisions_in_gate_block(tmp_path):
    assert _write(tmp_path, gate={"block": ["open_decisions"]}).gate["block"] == ["open_decisions"]


def test_unknown_gate_block_name_is_config_error_listing_valid_set(tmp_path):
    with pytest.raises(ConfigError) as e:
        _write(tmp_path, gate={"block": ["assertion", "bogus_check"]})
    assert "gate.block[1]: unknown check (valid: " in str(e.value) and "bogus_check" not in str(e.value)
    assert "(valid: " + ", ".join(GATE_CHECKS) + ")" in str(e.value)


def test_gate_block_names_are_case_sensitive(tmp_path):
    with pytest.raises(ConfigError, match=r"gate\.block\[0\]: unknown check") as e:
        _write(tmp_path, gate={"block": ["Assertion"]})
    assert "Assertion" not in str(e.value).replace("(valid: ", "").split("unknown check")[0]


@pytest.mark.parametrize("name", ["blocker", "major", "minor"])
def test_known_review_block_on_severity_accepted(tmp_path, name):
    assert _write(tmp_path, review_block_on=[name]).review_block_on == [name]


@pytest.mark.parametrize("bad", ["critical", "Blocker", ""])
def test_unknown_review_block_on_severity_is_config_error_listing_valid_set(tmp_path, bad):
    with pytest.raises(ConfigError) as e:
        _write(tmp_path, review_block_on=["major", bad])
    assert "review_block_on[1]: unknown severity (valid: " in str(e.value)
    assert not bad or bad not in str(e.value)
    assert "(valid: blocker, major, minor)" in str(e.value)
    assert REVIEW_SEVERITIES == ("blocker", "major", "minor")


def test_gate_checks_match_run_gate_output_keys(tmp_path):
    """run_gate asks `k in block` for every key of its findings, fired or not, so the sentinel records
    those keys: they must equal GATE_CHECKS. blocked_by (every check fires here) must match too."""
    class _Everything:  # True for every key, and remembers each key asked about
        def __init__(self):
            self.asked = set()

        def __contains__(self, key):
            self.asked.add(key)
            return True
    block = _Everything()
    ledger = (f"# DoD — US1001\n- **AC hash:** {ac_hash(_wi('old'))}\n{SECTION2}\n"
              "- [ ] Q1: which database? → _(open)_ · owner: lead\n"
              "## 3. AC → tests (traceability)\n| AC | acceptance criterion | tests | commit/PR | status |\n"
              "|---|---|---|---|---|\n| AC-1 | does X | Gone.testX |  | red |\n## 4. Gate log\n")
    r = run_gate(ledger, {}, {"BTest.cls": "class BTest {}"}, _wi("new"), block=block,
                 commits=(["abc1234"], None),
                 decisions_md="- [ ] D1: use paging → _(open)_ · kind: decision · why: unspecified\n")
    assert block.asked == set(GATE_CHECKS)
    assert sorted(r["blocked_by"]) == sorted(GATE_CHECKS)


def _wi(ac):
    return {"id": 1001, "fields": {"Microsoft.VSTS.Common.AcceptanceCriteria": ac}}


# ---- F31: control characters are rejected in every string of the (PR-supplied) config ----
from ado.config import STRICT_GATE_BLOCK, STRICT_REVIEW_BLOCK, ConfigInvalid  # noqa: E402

BAD_CHARS = {"newline": "\n", "nul": "\x00", "esc": "\x1b", "ls": "\u2028", "ps": "\u2029", "zwsp": "\u200b",
             "del": "\x7f", "c1": "\x85", "private": "\ue000", "rlo": "\u202e", "bidi-isolate": "\u2066",
             "bom": "\ufeff", "soft-hyphen": "\u00ad", "tag": "\U000e0041", "surrogate": "\ud800"}
BASE = {"organization": "contoso", "project": "Fabrikam"}
# (config-with-a-hostile-hole, key path in the message); `{}` is replaced by "a<bad>b"
HOLES = [
    ({"project": "{}"}, "project"),
    ({"repository": "{}"}, "repository"),
    ({"dod_path": "{}"}, "dod_path"),
    ({"done_state": "{}"}, "done_state"),
    ({"task_type": "{}"}, "task_type"),
    ({"story_type": "{}"}, "story_type"),
    ({"removed_state": "{}"}, "removed_state"),
    ({"branch_prefix": "{}"}, "branch_prefix"),
    ({"attachments_allow": [".md", "{}"]}, "attachments_allow[1]"),
    ({"stack": {"phases": {"{}": "x"}}}, "stack.phases"),
    ({"stack": {"phases": {"plan": "{}"}}}, "stack.phases.<entry #1>"),
    ({"gate": {"tests": {"{}": "x"}}}, "gate.tests"),
    ({"gate": {"tests": {"src/**": "{}"}}}, "gate.tests.<entry #1>"),
    ({"{}": 1}, "top level"),
]


def _fill(node, bad):
    if isinstance(node, str):
        return node.replace("{}", "a" + bad + "b")
    if isinstance(node, dict):
        return {_fill(k, bad): _fill(v, bad) for k, v in node.items()}
    if isinstance(node, list):
        return [_fill(v, bad) for v in node]
    return node


@pytest.mark.parametrize("name", BAD_CHARS)
@pytest.mark.parametrize("hole,where", HOLES)
def test_control_character_in_any_config_string_is_rejected(tmp_path, hole, where, name):
    bad = BAD_CHARS[name]
    (tmp_path / "ado.config.json").write_text(json.dumps({**BASE, **_fill(hole, bad)}))
    with pytest.raises(ConfigInvalid) as e:
        Config.load(start_dir=tmp_path)
    msg = str(e.value)
    assert "control character" in msg and where in msg, msg
    assert "a" + bad + "b" not in msg and bad not in msg  # never echoes the offending value
    assert "src/**" not in msg and "phases.plan" not in msg  # nor a user-chosen key


def test_control_character_is_rejected_in_required_keys_and_nested_lists(tmp_path):
    for cfg in ({"organization": "con\ntoso", "project": "Fabrikam"},
                {**BASE, "gate": {"block": ["x\n"]}},
                {**BASE, "stack": {"phases": {"plan": ["ok", ["deep "]]}}}):
        (tmp_path / "ado.config.json").write_text(json.dumps(cfg))
        with pytest.raises(ConfigInvalid, match="control character"):
            Config.load(start_dir=tmp_path)


GOOD_STRINGS = ["Équipe", "Terminé", "日本語", "می\u200cخواهم", "क्\u200dष", "a\u200eb\u200fc", "a\u00a0b",
                "\U0001F600", "\U0001F468\u200d\U0001F469\u200d\U0001F467", "\u0378", "\U0001FAE8"]  # last two: Cn


@pytest.mark.parametrize("s", GOOD_STRINGS)
def test_ordinary_non_ascii_text_and_unassigned_code_points_stay_valid(tmp_path, s):
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {**BASE, "project": s, "done_state": s, "gate": {"tests": {s + "/**": s}}}, ensure_ascii=False),
        encoding="utf-8")
    cfg = Config.load(start_dir=tmp_path)
    assert cfg.project == s and cfg.done_state == s


def _load(tmp_path, **extra):
    (tmp_path / "ado.config.json").write_text(json.dumps({**BASE, **extra}))
    return Config.load(start_dir=tmp_path)


def test_mode_defaults_to_advisory(tmp_path):
    cfg = _load(tmp_path)
    assert cfg.mode == "advisory"
    assert cfg.strict == {"test_command": None, "test_timeout": 300}


def test_mode_rejects_unknown_value(tmp_path):
    for bad in ("Strict", "off", "", None, True, ["strict"]):
        with pytest.raises(ConfigInvalid, match="'mode'"):
            _load(tmp_path, mode=bad)
    assert _load(tmp_path, mode="strict").mode == "strict"


def test_strict_object_keys_validated(tmp_path):
    cfg = _load(tmp_path, strict={"test_command": "pytest -q", "test_timeout": 60})
    assert cfg.strict == {"test_command": "pytest -q", "test_timeout": 60}
    assert _load(tmp_path, strict={"test_timeout": 3600}).strict == {"test_command": None, "test_timeout": 3600}
    for bad in ([], "x", None, 5):  # strict not an object
        with pytest.raises(ConfigInvalid, match="'strict' must be a JSON object"):
            _load(tmp_path, strict=bad)
    for strict, where in (({"test_command": ""}, "strict.test_command"), ({"test_command": 5}, "strict.test_command"),
                          ({"test_command": "x" * 501}, "strict.test_command"),
                          ({"test_timeout": 0}, "strict.test_timeout"), ({"test_timeout": 3601}, "strict.test_timeout"),
                          ({"test_timeout": "60"}, "strict.test_timeout"), ({"test_timeout": True}, "strict.test_timeout"),
                          ({"test_timeout": 1.5}, "strict.test_timeout")):
        with pytest.raises(ConfigInvalid, match=where.replace(".", r"\.")):
            _load(tmp_path, strict=strict)
    with pytest.raises(ConfigInvalid, match="control character"):
        _load(tmp_path, strict={"test_command": "pytest\n::error::x"})
    with pytest.raises(ConfigInvalid) as e:  # unknown key: named by position, never echoed
        _load(tmp_path, strict={"secret-key-name": 1})
    assert "strict entry #1" in str(e.value) and "secret-key-name" not in str(e.value)


def test_effective_lists_in_strict_union_project_and_defaults(tmp_path):
    cfg = _load(tmp_path, mode="strict", review_block_on=["major"], gate={"block": ["assertion", "ac_drift"]})
    assert cfg.effective_review_block_on() == ["major", "blocker"]
    assert cfg.effective_gate_block() == ["assertion", "ac_drift", "ac_traceability", "open_questions",
                                          "commits_without_story"]
    cfg = _load(tmp_path, mode="strict", review_block_on=["blocker"], gate={"block": list(STRICT_GATE_BLOCK)})
    assert cfg.effective_review_block_on() == ["blocker"] and cfg.effective_gate_block() == list(STRICT_GATE_BLOCK)
    cfg = _load(tmp_path, mode="strict")
    assert cfg.effective_review_block_on() == ["blocker"] and cfg.effective_gate_block() == list(STRICT_GATE_BLOCK)


def test_effective_lists_unchanged_in_advisory(tmp_path):
    cfg = _load(tmp_path, review_block_on=["major"], gate={"block": ["ac_drift"]})
    assert cfg.effective_review_block_on() == ["major"] and cfg.effective_gate_block() == ["ac_drift"]
    cfg = _load(tmp_path, mode="advisory")
    assert cfg.effective_review_block_on() == [] and cfg.effective_gate_block() == []
    cfg.review_block_on.append("minor")  # the returned lists are copies
    assert cfg.effective_review_block_on() == ["minor"]
    cfg.effective_gate_block().append("x")
    assert cfg.effective_gate_block() == []


def test_strict_gate_block_names_are_valid_checks():
    assert set(STRICT_GATE_BLOCK) <= set(GATE_CHECKS)
    assert set(STRICT_REVIEW_BLOCK) <= set(REVIEW_SEVERITIES)


# ---- V3-13: Config.load_from_ref (the target branch's config, read with git, same validator) ----
def _ref_repo(tmp_path, cfg='{"organization": "contoso", "project": "Fabrikam", "mode": "strict"}', name="ado.config.json"):
    from tests.conftest import commit, git
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "base", {"README.md": "x\n", **({name: cfg} if cfg is not None else {})})
    return tmp_path


def test_load_from_ref_reads_the_file_at_the_ref_not_the_working_tree(tmp_path, git_env, monkeypatch):
    from tests.conftest import git
    repo = _ref_repo(tmp_path)
    (repo / "ado.config.json").write_text('{"organization": "other", "project": "p"}')  # the working tree is the PR's: ignored
    monkeypatch.setenv("ADO_CONFIG", str(repo / "ado.config.json"))
    cfg = Config.load_from_ref("refs/heads/main", repo=str(repo))
    assert (cfg.organization, cfg.mode) == ("contoso", "strict")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    sha = git(repo, "rev-parse", "HEAD")
    for ref in ("origin/main", "main", sha):  # remote-tracking first, then a local branch, then a full commit id
        assert Config.load_from_ref(ref, repo=str(repo)).mode == "strict", ref


def test_load_from_ref_validates_like_load(tmp_path, git_env):
    from ado.config import ConfigInvalid
    bad = ['{"organization": "contoso"}', "[1]", "{not json", '{"organization": "contoso", "project": "p", "mode": "x"}',
           '{"organization": "contoso", "project": "p", "gate": {"block": ["nope"]}}',
           '{"organization": "contoso", "project": "p\\u0007"}']
    for text in bad:
        repo = tmp_path / str(abs(hash(text)))
        repo.mkdir()
        _ref_repo(repo, text)
        with pytest.raises(ConfigInvalid) as e:
            Config.load_from_ref("refs/heads/main", repo=str(repo))
        (repo / "ado.config.json").write_text(text)  # the same text through Config.load gives the same complaint
        with pytest.raises(ConfigInvalid) as e2:
            Config.load(start_dir=repo)
        assert str(e.value).split(": ", 1)[1] == str(e2.value).split(": ", 1)[1]
        assert str(repo) not in str(e.value) and "\x07" not in str(e.value)


def test_load_from_ref_is_none_when_the_file_does_not_exist_at_the_ref(tmp_path, git_env):
    repo = _ref_repo(tmp_path, cfg=None)
    assert Config.load_from_ref("refs/heads/main", repo=str(repo)) is None
    repo2 = tmp_path / "r2"
    repo2.mkdir()
    _ref_repo(repo2, '{"organization": "contoso", "project": "p"}', name="sub/ado.config.json")  # only a nested file
    assert Config.load_from_ref("refs/heads/main", repo=str(repo2)) is None


def test_load_from_ref_fails_closed(tmp_path, git_env, monkeypatch):
    from ado.config import ConfigInvalid
    from tests.conftest import commit, git
    repo = _ref_repo(tmp_path)
    git(repo, "tag", "v1")
    git(repo, "tag", "origin/main")  # a tag named like a remote-tracking branch is not the base
    for ref in ("", "nope", "origin/main", "v1", "refs/tags/v1", "--output=x", "-h", "--help", "HEAD", "main:ado.config.json",
                "refs/heads/nope", "abc123", "x" * 300):
        with pytest.raises(ConfigInvalid) as e:
            Config.load_from_ref(ref, repo=str(repo))
        assert ref not in str(e.value) or ref == ""
    assert not (repo / "x").exists()
    # not a repository at all
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(tmp_path / "missing"))
    # git cannot run
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(repo))


def test_load_from_ref_a_missing_blob_is_a_git_failure_not_an_absent_file(tmp_path, git_env):
    import os
    from ado.config import ConfigInvalid
    from tests.conftest import git
    repo = _ref_repo(tmp_path)
    blob = git(repo, "rev-parse", "main:ado.config.json")
    os.remove(repo / ".git" / "objects" / blob[:2] / blob[2:])
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(repo))


def test_load_from_ref_refuses_a_big_file_a_symlink_and_a_directory(tmp_path, git_env):
    import os
    from ado.config import ConfigInvalid
    from tests.conftest import commit, git
    pad = ' ' * (1024 * 1024)
    repo = _ref_repo(tmp_path, '{"organization": "contoso", "project": "p"}' + pad)  # 1 MiB + the object
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(repo))
    repo2 = tmp_path / "ok"
    repo2.mkdir()
    exact = '{"organization": "contoso", "project": "p"}'
    _ref_repo(repo2, exact + " " * (1024 * 1024 - len(exact)))  # exactly 1 MiB is fine
    assert Config.load_from_ref("refs/heads/main", repo=str(repo2)).organization == "contoso"
    repo3 = tmp_path / "link"
    repo3.mkdir()
    git(repo3, "init", "-q", "-b", "main")
    commit(repo3, "base", {"real.json": '{"organization": "contoso", "project": "p"}'})
    os.symlink("real.json", repo3 / "ado.config.json")
    commit(repo3, "link")
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(repo3))
    repo4 = tmp_path / "dir"
    repo4.mkdir()
    git(repo4, "init", "-q", "-b", "main")
    commit(repo4, "base", {"ado.config.json/inner": "x"})
    with pytest.raises(ConfigInvalid):
        Config.load_from_ref("refs/heads/main", repo=str(repo4))


def test_load_from_ref_ignores_an_inherited_git_dir(tmp_path, git_env, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    _ref_repo(other, '{"organization": "wrong", "project": "p"}')
    repo = tmp_path / "repo"
    repo.mkdir()
    _ref_repo(repo)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert Config.load_from_ref("refs/heads/main", repo=str(repo)).organization == "contoso"


def test_deeply_nested_config_is_invalid_not_a_crash(tmp_path, git_env):
    """A nested value that the parser or the validator cannot walk is ConfigInvalid, never an uncaught RecursionError (exit 1)."""
    from ado.config import ConfigInvalid
    refused = 0
    for depth in (200, 500, 900, 5000, 200000):  # the shallow ones are valid configs (an unknown key is allowed)
        text = '{"organization": "contoso", "project": "p", "x": ' + "[" * depth + "]" * depth + "}"
        repo = tmp_path / f"d{depth}"
        repo.mkdir()
        _ref_repo(repo, text)
        (repo / "ado.config.json").write_text(text)
        for load in (lambda: Config.load_from_ref("refs/heads/main", repo=str(repo)), lambda: Config.load(start_dir=repo)):
            try:
                load()
            except ConfigInvalid as e:
                refused += 1
                assert "[[[" not in str(e)
    assert refused >= 4  # at least the 5000 and 200000 levels, through both loaders
