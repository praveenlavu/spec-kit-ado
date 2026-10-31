import json
import os

import pytest

from ado import __main__ as cli
from ado import kits, strict
from ado.config import ConfigInvalid
from tests.conftest import git

KIT = ".specify/extensions/sf"


def _registry(*entries):
    return json.dumps({"schema_version": "1.0", "extensions": dict(entries)})


def _entry(commands=(), skills=(), **kw):
    return {"version": "1.0.0", "source": "local", "enabled": True,
            "registered_commands": {"claude": list(commands)} if commands else {}, "registered_skills": list(skills), **kw}


def _put(root, rel, data=b"x\n"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return p


@pytest.fixture
def repo(tmp_path, git_env):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Dana Fictional")
    git(tmp_path, "config", "user.email", "dana@contoso.example")
    _put(tmp_path, f"{KIT}/extension.yml", "id: sf\n")
    _put(tmp_path, f"{KIT}/commands/specify.md", "specify\n")
    _put(tmp_path, f"{KIT}/sf-config.yml", "org: contoso\n")
    _put(tmp_path, f"{KIT}/local-config.yml", "me: 1\n")
    _put(tmp_path, ".specify/extensions/sf-extra/extension.yml", "id: sf-extra\n")
    _put(tmp_path, ".claude/skills/speckit-sf-specify/SKILL.md", "skill sf\n")
    _put(tmp_path, ".claude/skills/speckit-sf-extra-run/SKILL.md", "skill extra\n")
    _put(tmp_path, ".specify/extensions/.registry", _registry(
        ("sf", _entry(commands=["speckit.sf.specify"])), ("sf-extra", _entry(commands=["speckit.sf-extra.run"]))))
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _head(repo):
    return git(repo, "rev-parse", "HEAD")


# ---- dir_digest ----
def test_digest_changes_on_one_byte(repo):
    before = kits.dir_digest(repo, "sf")
    assert len(before) == 64
    _put(repo, f"{KIT}/commands/specify.md", "specifY\n")
    assert kits.dir_digest(repo, "sf") != before


def test_digest_excludes_local_and_kit_config_files(repo):
    before = kits.dir_digest(repo, "sf")
    _put(repo, f"{KIT}/local-config.yml", "me: 2\n")
    _put(repo, f"{KIT}/sf-config.yml", "org: fabrikam\n")
    _put(repo, f"{KIT}/other-config.local.yml", "a: 1\n")
    assert kits.dir_digest(repo, "sf") == before
    _put(repo, f"{KIT}/other-config.yml", "a: 1\n")  # not an excluded name: it counts
    assert kits.dir_digest(repo, "sf") != before


@pytest.mark.parametrize("autocrlf", [False, True])
def test_digest_local_equals_committed(repo, autocrlf):
    if autocrlf:
        git(repo, "config", "core.autocrlf", "true")
    _put(repo, f"{KIT}/commands/crlf.md", b"one\r\ntwo\r\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "crlf")
    assert kits.dir_digest(repo, "sf") == kits.dir_digest_at(repo, _head(repo), "sf")


def test_empty_kit_dir_raises(repo):
    git(repo, "rm", "-q", f"{KIT}/extension.yml", f"{KIT}/commands/specify.md")
    (repo / ".specify/extensions/sf-extra/extension.yml").unlink()  # tracked but gone from the disk: unreadable, never skipped
    with pytest.raises(kits.KitError) as e:
        kits.dir_digest(repo, "sf-extra")
    assert e.value.reason == "unreadable"
    with pytest.raises(kits.KitError) as e:
        kits.dir_digest(repo, "sf")
    assert e.value.reason == "empty"
    with pytest.raises(kits.KitError) as e:  # the id grammar is checked before any path is built
        kits.dir_digest(repo, "../sf")
    assert e.value.reason == "unreadable"


def test_control_char_path_raises_before_hash_object(repo, monkeypatch):
    _put(repo, f"{KIT}/commands/a\nb.md")
    calls = []
    real = strict._git
    monkeypatch.setattr(strict, "_git", lambda r, *a, **k: calls.append(a) or real(r, *a, **k))
    with pytest.raises(kits.KitError) as e:
        kits.dir_digest(repo, "sf")
    assert e.value.reason == "control-char"
    assert calls and not any("hash-object" in a for a in calls)


def test_symlink_in_kit_dir_raises(repo, tmp_path_factory):
    os.symlink(repo / KIT / "extension.yml", repo / KIT / "commands" / "link.md")
    with pytest.raises(kits.KitError) as e:
        kits.dir_digest(repo, "sf")
    assert e.value.reason == "symlink"
    (repo / KIT / "commands" / "link.md").unlink()
    outside = tmp_path_factory.mktemp("elsewhere")
    _put(outside, "extension.yml")
    os.rename(repo / KIT, repo / "kit-moved")
    os.symlink(outside, repo / KIT)
    with pytest.raises(kits.KitError) as e:
        kits.dir_digest(repo, "sf")
    assert e.value.reason == "symlink"


def test_dev_install_symlinked_copy_raises(repo):
    reg = kits.installed(repo)
    sf = next(k for k in reg if k["id"] == "sf")
    before = kits.agents_sha256(repo, sf["registered"])
    skill = repo / ".claude/skills/speckit-sf-specify/SKILL.md"
    cache = repo / "cache.md"
    cache.write_bytes(skill.read_bytes())
    skill.unlink()
    os.symlink(cache, skill)
    with pytest.raises(kits.KitError) as e:
        kits.agents_sha256(repo, sf["registered"])
    assert e.value.reason == "symlink"
    skill.unlink()
    with pytest.raises(kits.KitError) as e:  # a registered copy that is gone is unreadable, never skipped
        kits.agents_sha256(repo, sf["registered"])
    assert e.value.reason == "unreadable"
    assert len(before) == 64


def test_agents_digest_uses_exact_names(repo):
    by_id = {k["id"]: k for k in kits.installed(repo)}
    before = kits.agents_sha256(repo, by_id["sf"]["registered"])
    _put(repo, ".claude/skills/speckit-sf-extra-run/SKILL.md", "edited\n")
    assert kits.agents_sha256(repo, by_id["sf"]["registered"]) == before
    assert kits.agents_sha256(repo, by_id["sf-extra"]["registered"]) != before
    _put(repo, ".claude/skills/speckit-sf-specify/SKILL.md", "edited sf\n")
    assert kits.agents_sha256(repo, by_id["sf"]["registered"]) != before
    # a Claude commands layout and a skills list work the same, by exact name
    _put(repo, ".claude/commands/speckit.sf.old.md", "old\n")
    _put(repo, ".claude/skills/speckit-sf-new/SKILL.md", "new\n")
    both = {"commands": ["speckit.sf.old"], "skills": ["speckit-sf-new"]}
    assert len(kits.agents_sha256(repo, both)) == 64
    with pytest.raises(kits.KitError):
        kits.agents_sha256(repo, {"commands": ["../x"], "skills": []})


# ---- the registry ----
def test_missing_enabled_counts_as_enabled(repo):
    entry = _entry(commands=["speckit.sf.specify"])
    del entry["enabled"]
    _put(repo, ".specify/extensions/.registry", _registry(("sf", entry), ("ado", _entry()), ("off", {**_entry(), "enabled": False})))
    got = {k["id"]: k for k in kits.installed(repo)}
    assert set(got) == {"sf", "off"}  # `ado` is not a kit
    assert got["sf"]["enabled"] is True and got["off"]["enabled"] is False
    assert got["sf"]["version"] == "1.0.0"
    assert got["sf"]["registered"] == {"commands": ["speckit.sf.specify"], "skills": []}


def test_bad_registry_id_is_unpinned(repo):
    _put(repo, ".specify/extensions/.registry", _registry(("sf", _entry(commands=["speckit.sf.specify"])), ("Bad_ID\n", _entry()),
                                                         ("odd", {"enabled": "yes"})))
    got = kits.installed(repo)
    assert [k["id"] for k in got] == ["sf", None, None] and [k.get("n") for k in got][1:] == [2, 3]
    lock = {"schema": 1, "kits": {}}
    out = kits.verify(repo, lock)
    assert [(k["id"], k["state"]) for k in out["kits"] if k["id"] is None] == [(None, "unpinned")] * 2
    assert "Bad_ID" not in json.dumps(out)
    assert sum(f["rule"] == "kit-unpinned" for f in out["findings"]) == 3


def test_registry_corrupt_or_symlink_fails_closed(repo):
    reg = repo / ".specify/extensions/.registry"
    assert kits.installed(repo)
    for bad in (b"{not json", b'{"extensions": {"a": {}, "a": {}}}', b"[]", b'{"extensions": []}', b"\xff\xfe", b"x" * ((1 << 20) + 1)):
        reg.write_bytes(bad)
        with pytest.raises(kits.KitError) as e:
            kits.installed(repo)
        assert e.value.reason == "unreadable", bad[:12]
    reg.unlink()
    os.symlink(repo / "elsewhere", reg)  # dangling symlink: never "no registry"
    with pytest.raises(kits.KitError) as e:
        kits.installed(repo)
    assert e.value.reason == "symlink"
    reg.unlink()
    assert kits.installed(repo) == []  # truly absent: nothing installed
    os.rename(repo / ".specify", repo / "dot-specify")
    os.symlink(repo / "dot-specify", repo / ".specify")
    with pytest.raises(kits.KitError) as e:
        kits.installed(repo)
    assert e.value.reason == "symlink"


# ---- the lockfile ----
def _lock(**over):
    entry = {"version": "1.0.0", "source": "https://example.com/contoso/sf", "catalog_sha256": None,
             "dir_digest": "a" * 64, "agents_sha256": "b" * 64, **over}
    return json.dumps({"schema": 1, "kits": {"sf": entry}}).encode()


def test_lock_source_and_version_validated_else_null():
    ok = kits.read_lock(_lock())["kits"]["sf"]
    assert ok["version"] == "1.0.0" and ok["source"] == "https://example.com/contoso/sf" and ok["dir_digest"] == "a" * 64
    for src in ("http://example.com/x", "https://user@example.com/x", "https://example.com/x?token=1", "javascript:alert(1)", "x" * 600, 7):
        assert kits.read_lock(_lock(source=src))["kits"]["sf"]["source"] is None, src
    for ver in ("1.0 0", "v" * 41, "", "1.0/../x", 3):
        assert kits.read_lock(_lock(version=ver))["kits"]["sf"]["version"] is None, ver
    for bad in (b"{", b'{"schema": 1, "kits": {"sf": {}, "sf": {}}}', b'{"schema": 2, "kits": {}}', b'{"schema": 1, "kits": {"Bad": {}}}',
                b'{"schema": 1, "kits": {"sf": 3}}', _lock(dir_digest="A" * 64), _lock(dir_digest="a" * 63), _lock(agents_sha256=None),
                _lock(catalog_sha256="zz"), _lock(version="1\x00")):
        with pytest.raises(ConfigInvalid):
            kits.read_lock(bad)
    assert kits.read_lock(_lock(catalog_sha256="c" * 64))["kits"]["sf"]["catalog_sha256"] == "c" * 64


def _pin(repo, kit_id="sf", **over):
    by_id = {k["id"]: k for k in kits.installed(repo)}
    e = {"version": "1.0.0", "source": None, "catalog_sha256": None, "dir_digest": kits.dir_digest(repo, kit_id),
         "agents_sha256": kits.agents_sha256(repo, by_id[kit_id]["registered"]), **over}
    return e


def test_verify_states(repo):
    lock = {"schema": 1, "kits": {"sf": _pin(repo), "gone": _pin(repo)}}
    out = kits.verify(repo, lock)
    states = {k["id"]: k["state"] for k in out["kits"]}
    assert states == {"sf": "pinned", "sf-extra": "unpinned", "gone": "stale"}
    assert [f["rule"] for f in out["findings"]] == ["kit-unpinned"] and out["findings"][0]["kit"] == "sf-extra"
    _put(repo, f"{KIT}/commands/specify.md", "changed\n")
    out = kits.verify(repo, lock)
    assert {k["id"]: k["state"] for k in out["kits"]}["sf"] == "changed"
    _put(repo, f"{KIT}/commands/specify.md", "specify\n")
    assert {k["id"]: k["state"] for k in kits.verify(repo, lock)["kits"]}["sf"] == "pinned"
    _put(repo, ".claude/skills/speckit-sf-specify/SKILL.md", "tampered\n")  # the agent copy is what runs
    assert {k["id"]: k["state"] for k in kits.verify(repo, lock)["kits"]}["sf"] == "changed"
    _put(repo, ".claude/skills/speckit-sf-specify/SKILL.md", "skill sf\n")
    lock["kits"]["sf"]["version"] = "2.0.0"  # the registry version no longer matches the lock
    assert {k["id"]: k["state"] for k in kits.verify(repo, lock)["kits"]}["sf"] == "changed"
    os.symlink(repo / "extension-copy", repo / KIT / "evil.md")  # a hostile kit file is changed, never a pass
    lock["kits"]["sf"]["version"] = "1.0.0"
    got = kits.verify(repo, lock)
    assert {k["id"]: k["state"] for k in got["kits"]}["sf"] == "changed" and {"rule": "kit-changed"}.items() <= got["findings"][0].items()
    assert "evil" not in json.dumps(got)


def test_disabled_kit_is_judged_like_an_enabled_one(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    lock = {"schema": 1, "kits": {i: _pin(repo, i) for i in ("sf", "sf-extra")}}
    (repo / kits.LOCK_FILE).write_text(json.dumps(lock))
    reg = repo / ".specify/extensions/.registry"
    reg.write_text(_registry(("sf", {**_entry(commands=["speckit.sf.specify"]), "enabled": False}),
                             ("sf-extra", _entry(commands=["speckit.sf-extra.run"]))))
    assert cli.main(["kits", "verify"]) == 0 and {k["state"] for k in json.loads(capsys.readouterr().out)["kits"]} == {"pinned"}
    _put(repo, f"{KIT}/commands/specify.md", "tampered\n")
    _put(repo, ".claude/skills/speckit-sf-specify/SKILL.md", "tampered\n")
    assert cli.main(["kits", "verify"]) == 2
    assert {k["id"]: k["state"] for k in json.loads(capsys.readouterr().out)["kits"]}["sf"] == "changed"
    reg.write_text(_registry(("sf-extra", _entry(commands=["speckit.sf-extra.run"]))))  # entry removed, files remain: stale
    assert {k["id"]: k["state"] for k in kits.verify(repo, lock)["kits"]}["sf"] == "stale"


@pytest.mark.parametrize("attrs", ["* ident\n", ".specify/** filter=lfs\n", "*.md working-tree-encoding=UTF-16\n", ".specify/** filter=x\n"])
def test_content_attributes_refused_and_no_filter_runs(repo, attrs):
    marker = repo.parent / (repo.name + "-marker")
    git(repo, "config", "filter.x.clean", f"sh -c 'touch {marker}; cat'")
    git(repo, "config", "filter.lfs.clean", f"sh -c 'touch {marker}; cat'")
    (repo / ".gitattributes").write_text(attrs)
    with pytest.raises(kits.KitError) as e:
        kits.dir_digest(repo, "sf")
    assert e.value.reason == "attributes" and not marker.exists()
    lock = {"schema": 1, "kits": {"sf": {"version": None, "source": None, "catalog_sha256": None, "dir_digest": "a" * 64, "agents_sha256": "b" * 64}}}
    row = next(k for k in kits.verify(repo, lock)["kits"] if k["id"] == "sf")
    assert row == {"id": "sf", "state": "changed", "reason": "attributes"} and not marker.exists()


def test_text_and_eol_attributes_stay_allowed(repo):
    (repo / ".gitattributes").write_text("* text=auto\n*.md eol=crlf\n*.yml -text\n")
    assert len(kits.dir_digest(repo, "sf")) == 64


# ---- dir_digest_at (the commit, as CI sees it) ----
def test_dir_digest_at_none_sentinel_and_untracked(repo):
    head = _head(repo)
    assert kits.dir_digest_at(repo, head, "nokit") is None
    assert kits.dir_digest_at(repo, head, "sf") == kits.dir_digest(repo, "sf")
    _put(repo, f"{KIT}/untracked.md")  # untracked files are not in the commit
    assert kits.dir_digest_at(repo, head, "sf") != kits.dir_digest(repo, "sf")
    os.symlink("extension.yml", repo / KIT / "commands" / "link.md")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    assert kits.dir_digest_at(repo, _head(repo), "sf") == "changed"
    with pytest.raises(ValueError):
        kits.dir_digest_at(repo, "HEAD", "sf")


def test_dir_digest_at_only_excluded_files_is_none(tmp_path, git_env):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Dana Fictional")
    git(tmp_path, "config", "user.email", "dana@contoso.example")
    _put(tmp_path, f"{KIT}/local-config.yml")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "x")
    assert kits.dir_digest_at(tmp_path, _head(tmp_path), "sf") is None


# ---- the CLI ----
def test_cli_kits_verify(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["kits", "verify"]) == 2  # two unpinned kits, no lockfile
    out = json.loads(capsys.readouterr().out)
    assert {k["state"] for k in out["kits"]} == {"unpinned"}
    lock = {"schema": 1, "kits": {i: _pin(repo, i) for i in ("sf", "sf-extra")}}
    (repo / kits.LOCK_FILE).write_text(json.dumps(lock))
    assert cli.main(["kits", "verify"]) == 0
    assert {k["state"] for k in json.loads(capsys.readouterr().out)["kits"]} == {"pinned"}
    _put(repo, f"{KIT}/extension.yml", "id: evil\n")
    assert cli.main(["kits", "verify"]) == 2
    capsys.readouterr()
    (repo / kits.LOCK_FILE).write_text("{")
    assert cli.main(["kits", "verify"]) == 2
    assert "error:" in capsys.readouterr().err
    (repo / kits.LOCK_FILE).unlink()
    (repo / ".specify/extensions/.registry").write_text("{")
    assert cli.main(["kits", "verify"]) == 2
    assert "unreadable" in capsys.readouterr().err
