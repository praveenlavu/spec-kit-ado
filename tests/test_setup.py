def test_run_setup_captures_stores_verifies(monkeypatch, tmp_path, capsys):
    import json
    from ado import setup_cmd, secretstore
    from tests.conftest import FakeClient

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    answers = iter(["contoso", "Fabrikam"])              # org, project
    made = {}

    def fake_client_factory(config):
        c = FakeClient(config, responses=[{"name": "Fabrikam"}])
        made["client"] = c
        return c

    monkeypatch.setattr(setup_cmd, "AdoClient", fake_client_factory)
    out = tmp_path / "ado.config.json"
    rc = setup_cmd.run_setup(
        input_fn=lambda _p: next(answers),
        getpass_fn=lambda _p: "supersecretPAT",
        out_path=out,
    )
    assert rc == 0
    # stored (via FileStore over tmp home) and verified read-only
    assert (tmp_path / ".azdo" / "contoso.token").read_text() == "supersecretPAT"
    assert "/_apis/projects/Fabrikam?api-version=7.1" in made["client"].calls[0]["path"]
    assert json.loads(out.read_text()) == {"organization": "contoso", "project": "Fabrikam"}
    # the PAT is never echoed
    assert "supersecretPAT" not in capsys.readouterr().out


def test_run_setup_aborts_on_empty_pat(monkeypatch, tmp_path):
    from ado import setup_cmd, secretstore
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    answers = iter(["contoso", "P"])
    rc = setup_cmd.run_setup(input_fn=lambda _p: next(answers),
                             getpass_fn=lambda _p: "   ",
                             out_path=tmp_path / "ado.config.json")
    assert rc == 2
    assert not (tmp_path / ".azdo" / "contoso.token").exists()


def _setup(monkeypatch, tmp_path, out, answers=("contoso", "Fabrikam"), **kw):
    from ado import setup_cmd, secretstore
    from tests.conftest import FakeClient
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    monkeypatch.setattr(setup_cmd, "AdoClient",
                        lambda cfg: FakeClient(cfg, responses=[{"name": "Fabrikam"}]))
    it = iter(answers)
    return setup_cmd.run_setup(input_fn=lambda _p: next(it),
                               getpass_fn=lambda _p: "supersecretPAT", out_path=out, **kw)


def test_setup_preserves_other_keys(monkeypatch, tmp_path):
    import json
    out = tmp_path / "ado.config.json"
    before = {"organization": "old", "project": "Old", "stack": {"kit": "k", "phases": {"plan": "x"}},
              "gate": {"block": True}, "done_state": "Closed"}
    out.write_text(json.dumps(before))
    assert _setup(monkeypatch, tmp_path, out) == 0
    after = json.loads(out.read_text())
    assert after["organization"] == "contoso" and after["project"] == "Fabrikam"
    assert {k: v for k, v in after.items() if k not in ("organization", "project")} == \
        {k: v for k, v in before.items() if k not in ("organization", "project")}
    assert "supersecretPAT" not in out.read_text()


def test_setup_empty_answer_keeps_existing_org_project(monkeypatch, tmp_path):
    import json
    out = tmp_path / "ado.config.json"
    out.write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", "stack": {}}))
    assert _setup(monkeypatch, tmp_path, out, answers=("", "")) == 0
    assert json.loads(out.read_text()) == {"organization": "contoso", "project": "Fabrikam", "stack": {}}


def test_setup_refuses_to_overwrite_invalid_json(monkeypatch, tmp_path):
    import pytest
    from ado.config import ConfigError
    out = tmp_path / "ado.config.json"
    out.write_bytes(b"{not json")
    with pytest.raises(ConfigError, match="ado.config.json"):
        _setup(monkeypatch, tmp_path, out)
    assert out.read_bytes() == b"{not json"
    assert not (tmp_path / ".azdo" / "contoso.token").exists()


def test_setup_refuses_non_object_json(monkeypatch, tmp_path):
    import pytest
    from ado.config import ConfigError
    out = tmp_path / "ado.config.json"
    out.write_bytes(b"[]")
    with pytest.raises(ConfigError, match="ado.config.json"):
        _setup(monkeypatch, tmp_path, out)
    assert out.read_bytes() == b"[]"


def _verify_harness(monkeypatch, tmp_path, verify):
    from ado import setup_cmd, secretstore
    from ado.auth import auth_header
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    seen = {}

    class VerifyClient:
        def __init__(self, cfg):
            self.cfg = cfg

        def get(self, path):
            seen["auth"] = auth_header(self.cfg.organization)["Authorization"]
            return verify()

    monkeypatch.setattr(setup_cmd, "AdoClient", VerifyClient)
    return setup_cmd, seen


def _run(setup_cmd, tmp_path, pat="newPAT"):
    it = iter(["contoso", "Fabrikam"])
    return setup_cmd.run_setup(input_fn=lambda _p: next(it), getpass_fn=lambda _p: pat,
                               out_path=tmp_path / "ado.config.json")


def test_setup_verifies_typed_pat_even_with_env_token(monkeypatch, tmp_path):
    import base64
    monkeypatch.setenv("ADO_TOKEN", "oldPAT")
    setup_cmd, seen = _verify_harness(monkeypatch, tmp_path, lambda: {"name": "Fabrikam"})
    assert _run(setup_cmd, tmp_path) == 0
    assert seen["auth"] == "Basic " + base64.b64encode(b":newPAT").decode()
    import os
    assert os.environ["ADO_TOKEN"] == "oldPAT"  # env restored after verify


def test_failed_verify_keeps_old_token(monkeypatch, tmp_path):
    import pytest
    from ado.client import AdoError

    def boom():
        raise AdoError("HTTP 401 Unauthorized")

    setup_cmd, _ = _verify_harness(monkeypatch, tmp_path, boom)
    monkeypatch.setattr(setup_cmd, "store_token",
                        lambda *a: pytest.fail("store_token called before verify succeeded"))
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    with pytest.raises(AdoError):
        _run(setup_cmd, tmp_path)
    assert not (tmp_path / "ado.config.json").exists()


def test_setup_rejects_bad_org_before_pat_prompt(monkeypatch, tmp_path):
    import pytest
    from ado import setup_cmd, secretstore
    from ado.config import ConfigError
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    it = iter(["../x", "Fabrikam"])
    out = tmp_path / "ado.config.json"
    with pytest.raises(ConfigError, match="organization"):
        setup_cmd.run_setup(input_fn=lambda _p: next(it),
                            getpass_fn=lambda _p: pytest.fail("PAT prompted for a bad org"),
                            out_path=out)
    assert not out.exists() and not (tmp_path / ".azdo").exists()


def test_setup_rejects_bad_org_default_from_existing_config(monkeypatch, tmp_path):
    import pytest
    from ado.config import ConfigError
    out = tmp_path / "ado.config.json"
    out.write_text('{"organization": "../x", "project": "P"}')
    with pytest.raises(ConfigError, match="organization"):
        _setup(monkeypatch, tmp_path, out, answers=("", ""))
    assert not (tmp_path / ".azdo").exists()


def test_failed_verify_restores_env_and_keeps_config_bytes(monkeypatch, tmp_path):
    import os
    import pytest
    from ado.client import AdoError

    def boom():
        raise AdoError("HTTP 401 Unauthorized")

    setup_cmd, _ = _verify_harness(monkeypatch, tmp_path, boom)
    out = tmp_path / "ado.config.json"
    out.write_bytes('{"organization":"contoso","project":"Old","x":"É"}'.encode("utf-8"))
    before = out.read_bytes()
    for prior in ("oldPAT", None):
        if prior is None:
            monkeypatch.delenv("ADO_TOKEN", raising=False)
        else:
            monkeypatch.setenv("ADO_TOKEN", prior)
        with pytest.raises(AdoError):
            _run(setup_cmd, tmp_path)
        assert os.environ.get("ADO_TOKEN") == prior
        assert out.read_bytes() == before


def _states_client(monkeypatch, tmp_path, story, story_states, task_states, answers=()):
    """Run setup against a router fake: category -> `story`, states per type."""
    import json
    from ado import setup_cmd, secretstore
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())

    def mk(pairs):
        return {"value": [{"name": n, "category": c} for n, c in pairs]}

    class Router:
        def __init__(self, cfg):
            self.calls = []

        def get(self, path):
            self.calls.append(path)
            if "/projects/" in path:
                return {"name": "Fabrikam"}
            if "RequirementCategory" in path:
                return {"defaultWorkItemType": {"name": story}}
            if f"/workitemtypes/{story.replace(' ', '%20')}/states" in path:
                return mk(story_states)
            if "/workitemtypes/Task/states" in path:
                return mk(task_states)
            raise AssertionError(path)

    monkeypatch.setattr(setup_cmd, "AdoClient", Router)
    it = iter(["contoso", "Fabrikam", *answers])
    out = tmp_path / "ado.config.json"
    rc = setup_cmd.run_setup(input_fn=lambda _p: next(it), getpass_fn=lambda _p: "pat", out_path=out)
    return rc, json.loads(out.read_text())


AGILE = [("New", "Proposed"), ("Active", "InProgress"), ("Closed", "Completed"),
         ("Removed", "Removed")]
SCRUM = [("New", "Proposed"), ("Done", "Completed"), ("Removed", "Removed")]


def test_setup_agile_writes_closed_and_removed(monkeypatch, tmp_path):
    rc, cfg = _states_client(monkeypatch, tmp_path, "User Story", AGILE, AGILE)
    assert rc == 0
    assert (cfg["story_type"], cfg["done_state"], cfg["removed_state"]) == \
        ("User Story", "Closed", "Removed")


def test_setup_scrum_writes_pbi_and_done(monkeypatch, tmp_path):
    _, cfg = _states_client(monkeypatch, tmp_path, "Product Backlog Item", SCRUM, SCRUM)
    assert (cfg["story_type"], cfg["done_state"]) == ("Product Backlog Item", "Done")


def test_setup_asks_when_story_and_task_disagree(monkeypatch, tmp_path):
    task = [("Done", "Completed"), ("Removed", "Removed")]
    _, cfg = _states_client(monkeypatch, tmp_path, "User Story", AGILE, task, answers=("Closed",))
    assert cfg["done_state"] == "Closed"


def test_setup_keeps_existing_state_keys(monkeypatch, tmp_path):
    import json
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "done_state": "Shipped"}))
    _, cfg = _states_client(monkeypatch, tmp_path, "User Story", AGILE, AGILE)
    assert cfg["done_state"] == "Shipped" and cfg["removed_state"] == "Removed"


def test_setup_lookup_failure_keeps_defaults(monkeypatch, tmp_path, capsys):
    from ado.client import AdoError
    from ado import setup_cmd, secretstore
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())

    class Flaky:
        def __init__(self, cfg):
            pass

        def get(self, path):
            if "/projects/" in path:
                return {"name": "Fabrikam"}
            raise AdoError("HTTP 403")

    monkeypatch.setattr(setup_cmd, "AdoClient", Flaky)
    it = iter(["contoso", "Fabrikam"])
    out = tmp_path / "ado.config.json"
    assert setup_cmd.run_setup(input_fn=lambda _p: next(it), getpass_fn=lambda _p: "pat",
                               out_path=out) == 0
    import json
    assert json.loads(out.read_text()) == {"organization": "contoso", "project": "Fabrikam"}
    assert "could not auto-detect" in capsys.readouterr().out


def test_setup_appends_ado_to_gitignore_once(monkeypatch, tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text("*.pyc")  # no trailing newline
    out = tmp_path / "ado.config.json"
    assert _setup(monkeypatch, tmp_path, out) == 0
    assert _setup(monkeypatch, tmp_path, out) == 0
    assert gi.read_text() == "*.pyc\n.ado/\n"


def test_setup_refuses_a_value_load_would_reject_and_stores_nothing(monkeypatch, tmp_path):
    import pytest
    from ado.config import ConfigError
    task = [("Done", "Completed"), ("Removed", "Removed")]
    out = tmp_path / "ado.config.json"
    with pytest.raises(ConfigError, match=r"done_state contains a control character") as e:
        _states_client(monkeypatch, tmp_path, "User Story", AGILE, task, answers=("Clo\x1b[2Jsed",))
    assert "\x1b" not in str(e.value)
    assert not out.exists()  # nothing written
    assert not (tmp_path / ".azdo").exists()  # token not stored


# ---- `ado setup --profile` (P4-6) ----
import json as _json
import pytest

_PROFILE_KEYS = {
    "starter": {"mode": "advisory"},
    "team": {"mode": "advisory", "gate": {"block": ["ac_traceability", "open_questions"]}, "review_block_on": ["blocker"]},
    "strict": {"mode": "strict", "strict": {"test_command": "python3 -m pytest -q"}},
}


@pytest.mark.parametrize("name", ["starter", "team", "strict"])
def test_profile_writes_exact_keys(monkeypatch, tmp_path, name):
    out = tmp_path / "ado.config.json"
    answers = ("contoso", "Fabrikam") + (("python3 -m pytest -q",) if name == "strict" else ())
    assert _setup(monkeypatch, tmp_path, out, answers, profile=name) == 0
    assert _json.loads(out.read_text()) == {"organization": "contoso", "project": "Fabrikam", **_PROFILE_KEYS[name]}
    from ado import setup_cmd
    assert set(setup_cmd.PROFILES) == {"starter", "team", "strict"}
    assert not list(tmp_path.glob("*.tmp"))


def test_strict_profile_leaves_test_command_when_profile_approved(monkeypatch, tmp_path):
    from ado import stack
    monkeypatch.setattr(stack, "read_profile", lambda root=".", blob=None: {"status": "approved", "profile": {}, "errors": []})
    out = tmp_path / "ado.config.json"
    assert _setup(monkeypatch, tmp_path, out, profile="strict") == 0  # a third answer would raise StopIteration
    assert _json.loads(out.read_text()) == {"organization": "contoso", "project": "Fabrikam", "mode": "strict"}


def test_strict_profile_keeps_a_test_command_already_set(monkeypatch, tmp_path):
    out = tmp_path / "ado.config.json"
    out.write_text(_json.dumps({"organization": "contoso", "project": "Fabrikam", "strict": {"test_command": "make test"}}))
    assert _setup(monkeypatch, tmp_path, out, ("", "", "y"), profile="strict", interactive=True) == 0  # no test-command question
    assert _json.loads(out.read_text())["strict"] == {"test_command": "make test"}


def _existing(tmp_path, **extra):
    out = tmp_path / "ado.config.json"
    out.write_bytes(_json.dumps({"organization": "contoso", "project": "Fabrikam", **extra}, indent=2).encode() + b"\n")
    return out


def test_existing_config_diff_and_no_write_on_n(monkeypatch, tmp_path, capsys):
    out = _existing(tmp_path, mode="strict", gate={"block": ["assertion"]})
    before = out.read_bytes()
    assert _setup(monkeypatch, tmp_path, out, ("", "", "n"), profile="team", interactive=True) == 2
    shown = capsys.readouterr().out
    assert 'mode: "strict" -> "advisory"' in shown
    assert 'gate.block: ["assertion"] -> ["assertion", "ac_traceability", "open_questions"]' in shown
    assert 'review_block_on: (not set) -> ["blocker"]' in shown
    assert out.read_bytes() == before
    assert not (tmp_path / ".azdo" / "contoso.token").exists()  # nothing at all was written


def test_existing_config_written_only_on_y(monkeypatch, tmp_path):
    out = _existing(tmp_path)
    assert _setup(monkeypatch, tmp_path, out, ("", "", "Y"), profile="starter", interactive=True) == 0
    assert _json.loads(out.read_text()) == {"organization": "contoso", "project": "Fabrikam", "mode": "advisory"}
    assert out.read_bytes().endswith(b"\n") and b'\n  "mode"' in out.read_bytes()  # indent kept


def test_existing_config_without_a_terminal_prints_the_diff_and_exits_2(monkeypatch, tmp_path, capsys):
    out = _existing(tmp_path)
    before = out.read_bytes()
    assert _setup(monkeypatch, tmp_path, out, profile="starter", interactive=False) == 2  # a prompt would raise StopIteration
    assert 'mode: (not set) -> "advisory"' in capsys.readouterr().out
    assert out.read_bytes() == before


def test_existing_config_anything_but_y_writes_nothing(monkeypatch, tmp_path):
    for ans in ("", "yes", "no", "n"):
        out = _existing(tmp_path)
        before = out.read_bytes()
        assert _setup(monkeypatch, tmp_path, out, ("", "", ans), profile="starter", interactive=True) == 2
        assert out.read_bytes() == before


def test_profile_never_removes_keys_and_keeps_crlf(monkeypatch, tmp_path):
    extra = {"gate": {"tests": {"*.py": "assert"}, "block": ["assertion"]}, "stack": {"kit": "k"}, "review_block_on": ["major"],
             "strict": {"test_timeout": 600}, "done_state": "Closed", "dod_path": "docs/dod"}
    out = tmp_path / "ado.config.json"
    out.write_bytes(_json.dumps({"organization": "contoso", "project": "Fabrikam", **extra}, indent=2).replace("\n", "\r\n").encode() + b"\r\n")
    for name, ans in (("team", ()), ("strict", ("python3 -m pytest -q",))):
        assert _setup(monkeypatch, tmp_path, out, ("", "", *ans, "y"), profile=name, interactive=True) == 0
        after = _json.loads(out.read_text())
        assert all(k in after for k in extra) and after["gate"]["tests"] == {"*.py": "assert"} and after["strict"]["test_timeout"] == 600
        assert after["gate"]["block"][0] == "assertion" and "major" in after["review_block_on"]
        raw = out.read_bytes()
        assert b"\r\n" in raw and raw.replace(b"\r\n", b"").count(b"\n") == 0


def test_diff_hides_long_and_secret_looking_values(monkeypatch, tmp_path, capsys):
    secret = "a" * 52  # ado-pat shape, a valid organization name
    out = _existing(tmp_path)
    out.write_bytes(out.read_bytes().replace(b"contoso", secret.encode()))
    assert _setup(monkeypatch, tmp_path, out, ("contoso", "", "n"), profile="starter", interactive=True) == 2
    shown = capsys.readouterr().out
    assert secret not in shown and "organization: changed" in shown and 'mode: (not set) -> "advisory"' in shown


def test_profile_refuses_a_symlinked_config_and_an_invalid_result(monkeypatch, tmp_path):
    from ado.config import ConfigError
    real = _existing(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ConfigError):
        _setup(monkeypatch, tmp_path, link, profile="starter", interactive=True)
    bad = _existing(tmp_path, gate={"block": ["nope"]})
    before = bad.read_bytes()
    with pytest.raises(ConfigError):
        _setup(monkeypatch, tmp_path, bad, ("", "", "y"), profile="team", interactive=True)
    assert bad.read_bytes() == before


def test_setup_profile_flag_is_wired_and_validated():
    from ado.__main__ import main
    with pytest.raises(SystemExit) as e:
        main(["setup", "--profile", "bogus"])
    assert e.value.code == 2


def test_prompts_show_existing_org_and_project_escaped(monkeypatch, tmp_path, capsys):
    from ado import setup_cmd, secretstore
    from tests.conftest import FakeClient
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    monkeypatch.setattr(setup_cmd, "AdoClient", lambda cfg: FakeClient(cfg, responses=[{"name": "Fabrikam"}]))
    evil = "\x1b]0;pwned\x07\x1b[31mred\nline2"
    out = tmp_path / "ado.config.json"
    out.write_text(_json.dumps({"organization": "contoso", "project": evil}))
    prompts, answers = [], iter(["", ""])
    with pytest.raises(Exception):  # the kept project holds a control character: refused at the end, after the prompts
        setup_cmd.run_setup(input_fn=lambda p: prompts.append(p) or next(answers), getpass_fn=lambda _p: "pat", out_path=out)
    assert prompts and all(c == "\n" or c.isprintable() for p in prompts for c in p)  # nothing but plain text
    assert "\x1b" not in "".join(prompts) and "\\u001b" in prompts[1]
    out.write_text(_json.dumps({"organization": "\x1b[2Jcontoso", "project": "Fabrikam"}))
    prompts.clear()
    with pytest.raises(Exception):  # the org is invalid: refused before any request, but the prompt was escaped
        setup_cmd.run_setup(input_fn=lambda p: prompts.append(p) or next(iter([""])), getpass_fn=lambda _p: "pat", out_path=out)
    assert "\x1b" not in prompts[0] and "\\u001b" in prompts[0]
    long = "p" * 500
    out.write_text(_json.dumps({"organization": "contoso", "project": long}))
    prompts.clear()
    answers = iter(["", ""])
    setup_cmd.run_setup(input_fn=lambda p: prompts.append(p) or next(answers), getpass_fn=lambda _p: "pat", out_path=out)
    assert long not in prompts[1] and len(prompts[1]) < 150


def test_diff_says_an_object_was_replaced_and_warns_when_strict_goes_off(monkeypatch, tmp_path, capsys):
    out = _existing(tmp_path, mode={"x": 1})
    assert _setup(monkeypatch, tmp_path, out, ("", "", "n"), profile="starter", interactive=True) == 2
    shown = capsys.readouterr().out
    assert 'mode: (an object, replaced) -> "advisory"' in shown and "(not set)" not in shown.split("mode:")[1].split("\n")[0]
    out = _existing(tmp_path, mode="strict")
    assert _setup(monkeypatch, tmp_path, out, ("", "", "n"), profile="starter", interactive=True) == 2
    shown = capsys.readouterr().out
    assert shown.index("Warning: this turns strict mode off") < shown.index("Nothing written") and 'mode: "strict" -> "advisory"' in shown


def test_nothing_to_change_does_not_say_wrote(monkeypatch, tmp_path, capsys):
    out = _existing(tmp_path, mode="advisory")
    before = out.read_bytes()
    assert _setup(monkeypatch, tmp_path, out, profile="starter", interactive=True) == 0  # no y asked: nothing to confirm
    shown = capsys.readouterr().out
    assert "nothing to change in" in shown and "wrote" not in shown and out.read_bytes() == before
