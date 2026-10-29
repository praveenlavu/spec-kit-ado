import os
import subprocess

from ado import secretstore as ss
from ado.secretstore import FallbackStore, FileStore, MacKeychain, resolve_store


def _home(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)


def test_filestore_roundtrip(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    s = FileStore()
    assert s.get("contoso") is None
    where = s.set("contoso", "pat123")
    assert "contoso.token" in where
    assert s.get("contoso") == "pat123"
    assert oct(os.stat(tmp_path / ".azdo" / "contoso.token").st_mode)[-3:] == "600"


def test_filestore_get_empty_is_none(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    d = tmp_path / ".azdo"
    d.mkdir()
    (d / "x.token").write_text("   \n")
    assert FileStore().get("x") is None


def test_filestore_loose_perms_warn(monkeypatch, tmp_path, capsys):
    _home(monkeypatch, tmp_path)
    s = FileStore()
    s.set("o", "t")
    os.chmod(tmp_path / ".azdo" / "o.token", 0o644)
    assert s.get("o") == "t"
    assert "chmod 600" in capsys.readouterr().err


def test_filestore_tight_perms_no_warning(monkeypatch, tmp_path, capsys):
    _home(monkeypatch, tmp_path)
    s = FileStore()
    s.set("o", "t")                       # set() applies chmod 600
    assert s.get("o") == "t"
    assert capsys.readouterr().err == ""  # 600 file -> no warning


def test_filestore_writes_private_dir_and_file(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    FileStore().set("o", "t")
    assert oct(os.stat(tmp_path / ".azdo").st_mode)[-3:] == "700"
    assert oct(os.stat(tmp_path / ".azdo" / "o.token").st_mode)[-3:] == "600"


def test_mac_get_parses_secret(monkeypatch):
    calls = {}

    def fake_run(args, **kw):
        calls["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="pat123\n", stderr="")

    monkeypatch.setattr(ss.subprocess, "run", fake_run)
    assert MacKeychain().get("contoso") == "pat123"
    assert calls["args"][:2] == ["security", "find-generic-password"]
    assert "ado-toolkit" in calls["args"] and "contoso" in calls["args"]


def test_mac_get_missing_returns_none(monkeypatch):
    monkeypatch.setattr(
        ss.subprocess, "run",
        lambda a, **kw: subprocess.CompletedProcess(a, 44, stdout="", stderr="not found"))
    assert MacKeychain().get("contoso") is None


def test_mac_set_uses_update_flag(monkeypatch):
    seen = {}

    def mock_run(a, **kw):
        seen["args"] = a
        return subprocess.CompletedProcess(a, 0, "", "")

    monkeypatch.setattr(ss.subprocess, "run", mock_run)
    where = MacKeychain().set("contoso", "pat123")
    assert "-U" in seen["args"] and "Keychain" in where


class _Boom:
    def get(self, o): raise RuntimeError("keychain down")
    def set(self, o, s): raise RuntimeError("keychain down")


def test_fallback_get_uses_file_when_primary_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    fb = FallbackStore(_Boom(), FileStore())
    fb.set("o", "tok")                 # primary raises -> file
    assert (tmp_path / ".azdo" / "o.token").is_file()
    assert fb.get("o") == "tok"        # primary raises -> file


def test_fallback_get_uses_file_when_primary_none(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    class _Empty:
        def get(self, o): return None
        def set(self, o, s): return "primary"
    FileStore().set("o", "fromfile")
    assert FallbackStore(_Empty(), FileStore()).get("o") == "fromfile"


def test_resolve_store_is_filestore_on_linux(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert isinstance(resolve_store(), FileStore)


def test_resolve_store_is_filestore_on_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert isinstance(resolve_store(), FileStore)


def test_filestore_refuses_path_traversal(monkeypatch, tmp_path):
    import pytest
    _home(monkeypatch, tmp_path)
    for bad in ("../x", "a/b", "", ".hidden"):
        with pytest.raises(ValueError, match="organization"):
            FileStore().set(bad, "t")
        with pytest.raises(ValueError, match="organization"):
            FileStore().get(bad)
    assert not (tmp_path / ".azdo").exists()
