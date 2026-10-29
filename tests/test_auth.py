import base64

import pytest

from ado.auth import AuthError, auth_header, load_token, store_token


def test_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("ADO_TOKEN", "envtok\n")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    d = tmp_path / ".azdo"
    d.mkdir()
    (d / "anyorg.token").write_text("filetok\n")
    assert load_token("anyorg") == "envtok"


def test_header_is_basic_b64(monkeypatch):
    monkeypatch.setenv("ADO_TOKEN", "pat123")
    h = auth_header("org")
    expected = base64.b64encode(b":pat123").decode()
    assert h == {"Authorization": f"Basic {expected}"}


def test_store_token_wins_over_missing(monkeypatch, tmp_path):
    from ado import auth, secretstore
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    auth.store_token("contoso", "pat123")
    assert auth.load_token("contoso") == "pat123"


def test_env_still_wins_over_store(monkeypatch, tmp_path):
    from ado import auth, secretstore
    monkeypatch.setenv("ADO_TOKEN", "envtok")
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    auth.store_token("contoso", "stored")
    assert auth.load_token("contoso") == "envtok"


def test_missing_everywhere_raises_no_leak(monkeypatch, tmp_path):
    from ado import auth, secretstore
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    with pytest.raises(auth.AuthError) as e:
        auth.load_token("contoso")
    assert "ADO_TOKEN" in str(e.value)


def test_missing_credential_error_never_leaks_and_instructs(monkeypatch, tmp_path):
    from ado import auth, secretstore
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    monkeypatch.setattr(secretstore, "resolve_store", lambda: secretstore.FileStore())
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    with pytest.raises(AuthError) as exc:
        load_token("contoso")
    msg = str(exc.value)
    assert "ADO_TOKEN" in msg and "ado setup" in msg
