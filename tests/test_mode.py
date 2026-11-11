"""`ado mode` (piece 5, S-4): the role the person at the keyboard has picked. Local state in `.ado/mode.json`; it steers prompts only, so no
enforcing command may ever read it. Fictional identities only."""
import json
import re
from pathlib import Path

import pytest

from ado import __main__ as _main
from ado import mode
from ado.client import ANONYMOUS_ID, AdoError
from ado.config import Trust

SRC = Path(__file__).resolve().parent.parent / "src" / "ado"
ME = "0a0a0a0a-0b0b-0c0c-0d0d-0e0e0e0e0e0e"
OTHER = "11111111-2222-3333-4444-555555555555"
ROLES = {"map": {ME: ["qa", "dev"], OTHER: ["bpo"]}, "signoff": ["qa"]}
FILE = Path(".ado/mode.json")


class Client:
    def __init__(self, user=None, error=None):
        self.user, self.error, self.calls = user, error, []

    def get(self, path):
        self.calls.append(path)
        if self.error:
            raise self.error
        return {"authenticatedUser": self.user}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust([], "terminal", {}))
    monkeypatch.setattr(_main, "AdoClient", lambda c: pytest.fail("no roles map: no identity is read"))
    return tmp_path


def run(capsys, *argv):
    code = _main.main(["mode", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_show_with_no_file_is_none(repo, capsys):
    assert run(capsys) == (0, "role: none\n", "")
    code, out, _ = run(capsys, "--json")
    assert code == 0 and json.loads(out) == {"role": None}
    assert not FILE.exists()  # a read never creates the file


@pytest.mark.parametrize("role", ["bpo", "tpo", "dev", "qa"])
def test_set_show_clear(repo, capsys, role):
    assert run(capsys, role) == (0, f"role: {role}\n", "")
    assert json.loads(FILE.read_text()) == {"role": role}
    assert run(capsys) == (0, f"role: {role}\n", "")
    assert json.loads(run(capsys, "--json")[1]) == {"role": role}
    assert run(capsys, "--clear") == (0, "role: none\n", "")
    assert not FILE.exists()
    assert run(capsys, "--clear")[0] == 0  # clearing nothing is fine


def test_a_role_can_change_in_one_session(repo, capsys):
    run(capsys, "tpo")
    assert run(capsys, "dev")[0] == 0 and mode.read() == "dev"  # the TPO hand-off to dev


def test_unknown_role_and_conflicting_flags_are_usage_errors(repo):
    for argv in (["bsa"], ["QA"], ["qa", "--clear"], ["--clear", "--require", "qa"], ["qa", "--require", "qa"]):
        with pytest.raises(SystemExit) as e:
            _main.main(["mode", *argv])
        assert e.value.code == 2
    assert not FILE.exists()


def test_require(repo, capsys):
    assert run(capsys, "--require", "dev")[0] == 0  # no role set: nothing to stop
    run(capsys, "dev")
    assert run(capsys, "--require", "dev")[0] == 0
    assert run(capsys, "--require", "tpo,dev")[0] == 0
    code, out, err = run(capsys, "--require", "tpo,qa")
    assert code == 2 and out == "" and err.strip() == "error: " + mode.E_REQUIRE.format(roles="tpo, qa")
    assert ".ado/mode.json" in err
    for bad in ("", "dev,", "dev,dev", "bsa", "dev qa", "DEV"):
        assert run(capsys, "--require", bad)[0] == 2
    assert mode.read() == "dev"  # --require never writes


@pytest.mark.parametrize("text", ["", "x", "[]", "{}", '{"role": "bsa"}', '{"role": null}', '{"role": "qa", "set": 1}', '{"role": ["qa"]}', "\x00", '{"role": "qa"}\n\n' + " " * 5000])
def test_a_bad_mode_file_is_refused_not_guessed(repo, capsys, text):
    FILE.parent.mkdir()
    FILE.write_text(text)
    for argv in ((), ("--json",), ("--require", "qa")):
        code, out, err = run(capsys, *argv)
        assert (code, out) == (2, "") and err.strip() == "error: " + mode.E_FILE
    assert run(capsys, "dev")[0] == 0 and mode.read() == "dev"  # picking a role replaces it
    FILE.write_text(text)
    assert run(capsys, "--clear")[0] == 0 and not FILE.exists()


def test_a_symlinked_mode_file_or_dir_is_refused(repo, capsys):
    FILE.parent.mkdir()
    secret = repo / "elsewhere.json"
    secret.write_text('{"role": "qa"}')
    FILE.symlink_to(secret)
    assert run(capsys)[0] == 2 and run(capsys)[2].strip() == "error: " + mode.E_FILE
    FILE.unlink()
    (repo / "real").mkdir()
    (repo / ".ado").rmdir()
    (repo / ".ado").symlink_to(repo / "real")
    code, _, err = run(capsys, "qa")
    assert code == 2 and err.strip() == "error: " + mode.E_FILE and not (repo / "real/mode.json").exists()


def test_write_leaves_no_temp_files(repo, capsys):
    run(capsys, "qa")
    run(capsys, "dev")
    assert [p.name for p in FILE.parent.iterdir()] == ["mode.json"]


# ---- identity limit (only with a roles map on the target branch) -------------------------------------------------------------------------------------

def _with_roles(monkeypatch, client):
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust([ME, OTHER], "comment", {}, ROLES))
    monkeypatch.setattr(_main, "AdoClient", lambda c: client)


def test_with_a_roles_map_the_token_identity_must_hold_the_role(repo, capsys, monkeypatch):
    client = Client({"id": ME.upper()})  # ids compare lowercase
    _with_roles(monkeypatch, client)
    assert run(capsys, "qa") == (0, "role: qa\n", "") and mode.read() == "qa"
    assert run(capsys, "dev")[0] == 0
    code, out, err = run(capsys, "bpo")
    assert (code, out) == (2, "") and err.strip() == "error: " + mode.E_NOT_HELD.format(role="bpo") and mode.read() == "dev"  # refused: nothing written
    assert run(capsys, "tpo")[0] == 2 and mode.read() == "dev"  # held by nobody
    assert all(c == "/_apis/connectionData?api-version=7.1-preview" for c in client.calls)


@pytest.mark.parametrize("user", [{"id": ANONYMOUS_ID}, {"id": OTHER.replace("1", "2")}, {"id": ME, "descriptor": "System:PublicAccess;x"}, {}, None, {"id": 7}])
def test_unmapped_anonymous_or_unreadable_identity_writes_nothing(repo, capsys, monkeypatch, user):
    _with_roles(monkeypatch, Client(user))
    code, _, err = run(capsys, "qa")
    assert code == 2 and err.startswith("error: ") and not FILE.exists()


def test_a_network_error_writes_nothing_and_exits_1(repo, capsys, monkeypatch):
    _with_roles(monkeypatch, Client(error=AdoError("HTTP 503")))
    code, out, err = run(capsys, "qa")
    assert (code, out) == (1, "") and err.startswith("error: ") and not FILE.exists()


def test_show_clear_and_require_never_ask_ado(repo, capsys, monkeypatch):
    _with_roles(monkeypatch, Client(error=AdoError("must not be called")))
    FILE.parent.mkdir()
    FILE.write_text('{"role": "bpo"}')  # not held by ME, but only picking checks identity
    assert run(capsys)[0] == 0 and run(capsys, "--require", "bpo")[0] == 0 and run(capsys, "--clear")[0] == 0


# ---- nothing that enforces reads the mode ------------------------------------------------------------------------------------------------------------

def test_only_the_mode_verb_touches_the_mode_file():
    holders = sorted(p.name for p in SRC.glob("*.py") if "mode.json" in p.read_text(encoding="utf-8"))
    assert holders == ["mode.py"]
    users = sorted(p.name for p in SRC.glob("*.py") if re.search(r"\bmode\.(read|require|write|clear)\b|from \. import .*\bmode\b|from \.mode\b", p.read_text(encoding="utf-8")))
    assert users == ["__main__.py"]
    main_src = (SRC / "__main__.py").read_text(encoding="utf-8")
    assert len(re.findall(r"\bmode\.(?:read|require|write|clear|check_identity)\(", main_src)) == len(re.findall(r"\bmode\.\w+\(", main_src))
    assert main_src.count("_mode(args)") == 2  # the definition's call site and the one dispatch line: no other command goes through it


def test_a_tampered_mode_file_changes_no_enforcing_output(repo, capsys, monkeypatch):
    monkeypatch.setattr(mode, "read", lambda *a, **k: pytest.fail("an enforcing command opened the mode file"))
    FILE.parent.mkdir()
    FILE.write_text("not json at all")
    assert _main.main(["strict", "mode"]) == 0
    clean = capsys.readouterr().out
    FILE.unlink()
    assert _main.main(["strict", "mode"]) == 0 and capsys.readouterr().out == clean
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust([], "terminal", {}))
    for gate in ("review-spec", "approve-pr"):
        FILE.write_text('{"role": "bpo"}')
        assert _main.main(["approvals", "check", "1001", "--gate", gate]) == 0
        assert json.loads(capsys.readouterr().out) == {"gate": gate, "mode": "terminal", "met": True}
