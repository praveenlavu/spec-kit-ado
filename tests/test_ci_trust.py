# V3-13: CI trust. With `--base REF`, `gate`, `strict check` and `strict verify` use the config of the PR's target branch when it is
# strict there: a PR cannot weaken (or re-point) its own strictness. Real temp git repos; the target branch is refs/remotes/origin/main.
import hashlib
import json
import os
import subprocess

import pytest

from ado import __main__ as m
from ado import decisions, strict
from ado.__main__ import main
from ado.dod import SECTION2
from tests.conftest import FakeClient, commit, git
from tests.test_strict import CMD, LEDGER, RED_NOTE, _d, _ledger_rows, _pair, _project, _repo
from tests.test_strict_wire import _full, _wire

ORG = {"organization": "contoso", "project": "Fabrikam"}
STRICT = {**ORG, "mode": "strict"}
LINE = "config: target branch (strict)"


def _cfg_text(cfg):
    return cfg if isinstance(cfg, str) else json.dumps(cfg)


# ---- gate ----
def _gate_pr(tmp_path, monkeypatch, git_env, target, head, decisions_md=None):
    """main holds `target` (a dict, raw text or None) = refs/remotes/origin/main; the branch `feature` (checked out) holds `head` and
    a ledger with one AC whose test does not exist (AC traceability finds it) and, optionally, a decisions file."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "AB#1001 base", {"README.md": "x\n", **({"ado.config.json": _cfg_text(target)} if target is not None else {})})
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    files = {"dod/DoD_1001.md": f"{SECTION2}\n## 3. AC\n| AC | x | tests | ev | status |\n|---|---|---|---|---|\n| AC-1 | x | Gone.testX | | red |\n"}
    if head is not None:
        files["ado.config.json"] = _cfg_text(head)
    if decisions_md is not None:
        files["dod/Decisions_1001.md"] = decisions_md
    commit(tmp_path, "AB#1001 feature", files)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    return str(tmp_path / "out" / "gate.md")


def _gate(capsys, summary, *extra):
    rc = main(["gate", "1001", "--offline", "--src", ".", "--summary-out", summary, *extra])
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def _md(summary):
    return open(summary, encoding="utf-8").read()


def test_target_strict_overrides_head_advisory(tmp_path, monkeypatch, capsys, git_env):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG)  # the PR says advisory
    rc, out = _gate(capsys, summary, "--base", "origin/main")
    assert rc == 1 and out["blocking"] is True and "ac_traceability" in out["blocked_by"]  # judged strict: the floor blocks
    assert _gate(capsys, summary)[0] == 0  # the head config alone (no --base): advisory, v0.2


def test_head_cannot_lower_gate_block(tmp_path, monkeypatch, capsys, git_env):
    dec = decisions.render_file(1001) + _d(1, "use the v2 api")  # an open decision
    target = {**STRICT, "gate": {"block": ["open_decisions"]}}  # a project-chosen entry beyond the strict floor
    head = {**ORG, "gate": {"block": []}, "review_block_on": []}
    summary = _gate_pr(tmp_path, monkeypatch, git_env, target, head, dec)
    rc, out = _gate(capsys, summary, "--base", "origin/main")
    assert rc == 1 and {"open_decisions", "ac_traceability"} <= set(out["blocked_by"])
    rc, out = _gate(capsys, summary)  # without --base the PR's own lower list applies (v0.2)
    assert rc == 0 and out["blocked_by"] == []


def test_target_missing_or_advisory_uses_head(tmp_path, monkeypatch, capsys, git_env):
    head = {**ORG, "gate": {"block": ["ac_traceability"]}}
    for target in (None, ORG, {**ORG, "mode": "advisory", "gate": {"block": []}}):
        repo = tmp_path / f"r{len(os.listdir(tmp_path))}"
        repo.mkdir()
        summary = _gate_pr(repo, monkeypatch, git_env, target, head)
        rc, out = _gate(capsys, summary, "--base", "origin/main")
        assert rc == 1 and out["blocked_by"] == ["ac_traceability"], target  # the head's own list, as in v0.2
        assert "config" not in out and LINE not in _md(summary) and "config:" not in _md(summary)
    repo = tmp_path / "strict-head"  # a head that adds strictness is honoured when the target has none
    repo.mkdir()
    summary = _gate_pr(repo, monkeypatch, git_env, None, STRICT)
    rc, out = _gate(capsys, summary, "--base", "origin/main")
    assert rc == 1 and "ac_traceability" in out["blocked_by"] and out["config"] == "pull request"  # judged by the PR's own strict file


@pytest.mark.parametrize("target", ['{"organization": "contoso"}', "{not json", "[]", '{"organization": "contoso", "project": "p", "mode": "lax"}'])
def test_target_invalid_exit_3(tmp_path, monkeypatch, capsys, git_env, target):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, target, ORG)  # a valid head config does not rescue it
    rc, out = _gate(capsys, summary, "--base", "origin/main")
    assert rc == 3 and out is None
    md = _md(summary)
    assert "ado.config.json is invalid" in md and str(tmp_path) not in md and "origin/main" not in md.replace("target branch", "")


def test_git_failure_exit_3(tmp_path, monkeypatch, capsys, git_env):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, STRICT)  # git cannot run: the base cannot be resolved, the PR's config is strict
    monkeypatch.setenv("PATH", "")  # git cannot be run at all
    assert _gate(capsys, summary, "--base", "origin/main")[0] == 3
    assert "ado.config.json is invalid" in _md(summary)
    monkeypatch.undo()
    monkeypatch.chdir(tmp_path)
    blob = git(tmp_path, "rev-parse", "origin/main:ado.config.json")  # the object is gone: git fails, the file is not "absent"
    os.remove(tmp_path / ".git" / "objects" / blob[:2] / blob[2:])
    assert _gate(capsys, summary, "--base", "origin/main")[0] == 3
    assert "ado.config.json is invalid" in _md(summary)


@pytest.mark.parametrize("base", ["origin/nope", "nope", "", "--output=x", "origin/main:ado.config.json", "abc"])
def test_unresolvable_base_exit_3(tmp_path, monkeypatch, capsys, git_env, base):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, STRICT)  # an advisory PR config keeps v0.2: see the next tests
    assert _gate(capsys, summary, f"--base={base}")[0] == 3
    assert not (tmp_path / "x").exists()


def test_tag_named_like_the_base_cannot_shadow(tmp_path, monkeypatch, capsys, git_env):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG)
    commit(tmp_path, "no story reference", {"extra.txt": "x\n"})
    git(tmp_path, "tag", "origin/main", "HEAD")  # a tag on the PR's own (lax) commit, named like the remote-tracking branch
    git(tmp_path, "tag", "main", "HEAD")
    rc, out = _gate(capsys, summary, "--base", "origin/main")
    assert rc == 1 and "ac_traceability" in out["blocked_by"] and out["config"] == "target branch (strict)"
    assert len(out["commits_without_story"]) == 1 and "commits_without_story" in out["blocked_by"]  # the range is origin/main's, not the tag's
    assert LINE in _md(summary)


def test_advisory_head_unresolvable_base_keeps_v02(tmp_path, monkeypatch, capsys, git_env):
    head = {**ORG, "gate": {"block": ["ac_traceability"]}}
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, head)  # the target is strict, but the base given cannot be read here
    short = git(tmp_path, "rev-parse", "--short", "HEAD")
    for base in ("origin/nosuch", "HEAD~5", short, "nosuch"):
        rc, out = _gate(capsys, summary, "--base", base)
        assert rc == 1 and out["blocked_by"] == ["ac_traceability"], base  # blocks on the PR's own list, as v0.2
        assert out["commits_without_story"] == [] and "config" not in out and "config:" not in _md(summary), base
        assert capsys.readouterr().err == ""
    git(tmp_path, "update-ref", "-d", "refs/remotes/origin/main")  # the lifecycle workflow's fallback: origin/main in a repo with no origin
    rc, out = _gate(capsys, summary, "--base", "origin/main")
    assert rc == 1 and "config" not in out
    mod = m  # strict check with an advisory head and an unknown base: v0.2 answers (advisory: ok)
    monkeypatch.setattr(mod, "AdoClient", lambda c: FakeClient(c))
    for phase in ("pr", "done"):
        rc, out = _cli(mod, capsys, "strict", "check", "1001", "--phase", phase, "--base", "origin/nosuch")
        assert rc == 0 and out["mode"] == "advisory"
    rc, _ = _cli(mod, capsys, "branch", "--id-from", "feature/ab1001-x", "--base", "origin/nosuch")
    assert rc == 0


def test_strict_head_unresolvable_base_exit_3(tmp_path, monkeypatch, capsys, git_env):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, STRICT)
    assert _gate(capsys, summary, "--base", "origin/nosuch")[0] == 3
    assert "ado.config.json is invalid" in _md(summary)
    monkeypatch.setattr(m, "AdoClient", lambda c: FakeClient(c))
    for argv in (["strict", "check", "1001", "--phase", "pr"], ["strict", "verify", "1001", "--no-record"]):
        rc, out = _cli(m, capsys, *argv, "--base", "origin/nosuch")
        assert rc == 2 and out is None  # an error: line on stderr, no JSON
    assert _cli(m, capsys, "branch", "--id-from", "feature/ab1001-x", "--base", "origin/nosuch")[0] == 3


def test_a_resolvable_base_with_an_unreadable_target_fails_closed_even_for_an_advisory_head(tmp_path, monkeypatch, capsys, git_env):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, "{not json", ORG)
    assert _gate(capsys, summary, "--base", "origin/main")[0] == 3  # the base is fine: the target's file is the problem


def test_summary_says_pull_request_when_the_prs_own_strict_config_judged(tmp_path, monkeypatch, capsys, git_env):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, None, STRICT)  # the first PR that introduces strict mode
    rc, out = _gate(capsys, summary, "--base", "origin/main")
    assert out["config"] == "pull request" and _md(summary).splitlines().count("config: pull request") == 1
    assert _gate(capsys, summary)[1].get("config") is None  # no --base: nothing added


def test_branch_id_from_base_uses_the_target_config(tmp_path, monkeypatch, capsys, git_env):
    _gate_pr(tmp_path, monkeypatch, git_env, STRICT, {**ORG, "branch_prefix": "x/"})  # the PR re-points branch_prefix
    argv = ["branch", "--id-from", "feature/ab1001-thing"]
    assert _cli(m, capsys, *argv)[0] == 1  # the PR's config alone: no story id, the template would skip the gate
    rc, out = _cli(m, capsys, *argv, "--base", "origin/main")
    assert (rc, out) == (0, 1001)
    (tmp_path / "ado.config.json").unlink()  # the PR deletes its config: exit 2 without --base
    assert _cli(m, capsys, *argv)[0] == 2
    assert _cli(m, capsys, *argv, "--base", "origin/main") == (0, 1001)
    git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "ado.config.json").write_text("{not json")  # an invalid target: fail closed
    git(tmp_path, "commit", "-qam", "AB#1001 break")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert _cli(m, capsys, *argv, "--base", "origin/main")[0] == 3


def test_branch_id_from_advisory_or_absent_target_is_v02(tmp_path, monkeypatch, capsys, git_env):
    for n, target in enumerate((None, ORG)):
        repo = tmp_path / f"r{n}"
        repo.mkdir()
        _gate_pr(repo, monkeypatch, git_env, target, {**ORG, "branch_prefix": "x/"})
        argv = ["branch", "--id-from", "feature/ab1001-thing"]
        assert _cli(m, capsys, *argv, "--base", "origin/main")[0] == 1  # the head's prefix applies, exactly as without --base
        assert _cli(m, capsys, "branch", "--id-from", "x/ab7-thing", "--base", "origin/main") == (0, 7)


def test_branch_base_needs_id_from(capsys):
    with pytest.raises(SystemExit):
        main(["branch", "1001", "--base", "origin/main"])
    assert "--base" in capsys.readouterr().err


def test_summary_names_the_config_source(tmp_path, monkeypatch, capsys, git_env):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG)
    _gate(capsys, summary, "--base", "origin/main")
    assert _md(summary).splitlines().count(LINE) == 1  # exactly one extra line
    (tmp_path / "dod" / "DoD_1001.md").unlink()  # the ledger-missing note shape names it too
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "AB#1001 drop")
    _gate(capsys, summary, "--base", "origin/main")
    assert _md(summary).splitlines().count(LINE) == 1
    other = tmp_path / "advisory"
    other.mkdir()
    s2 = _gate_pr(other, monkeypatch, git_env, None, ORG)
    _gate(capsys, s2, "--base", "origin/main")
    assert "config:" not in _md(s2)  # the head's own config: nothing extra


def test_no_base_flag_unchanged(tmp_path, monkeypatch, capsys, git_env):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG)  # a strict target exists, but no --base is given
    monkeypatch.setattr(m.Config, "load_from_ref", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no --base: no ref is read")))
    rc, out = _gate(capsys, summary)
    assert rc == 0 and "config" not in out and out["blocked_by"] == []  # the PR's own advisory config, as in v0.2
    assert "config:" not in _md(summary)


# ---- strict check / strict verify ----
def _target(repo, cfg, parent, ref="refs/remotes/origin/trunk"):
    """A commit with `ado.config.json` = cfg on top of `parent` (not on the checked-out branch), set as `ref`. cfg None: the parent as it is."""
    if cfg is None:
        git(repo, "update-ref", ref, parent)
        return
    env = {**os.environ, "GIT_INDEX_FILE": str(repo / ".git" / "trust-index")}

    def run(*a, inp=None):
        return subprocess.run(["git", "-C", str(repo), *a], env=env, input=inp, capture_output=True, text=True, check=True).stdout.strip()
    run("read-tree", parent)
    blob = run("hash-object", "-w", "--stdin", inp=_cfg_text(cfg))
    run("update-index", "--add", "--cacheinfo", f"100644,{blob},ado.config.json")
    sha = run("commit-tree", run("write-tree"), "-p", parent, "-m", "target")
    git(repo, "update-ref", ref, sha)
    os.remove(repo / ".git" / "trust-index")


def _cli(mod, capsys, *argv):
    rc = mod.main(list(argv))
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def test_strict_check_base_uses_the_target_config(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, mode=None)  # the PR's config says advisory
    _target(tmp_path, {**STRICT, "strict": {"test_command": CMD}}, _full(tmp_path, s.base))
    argv = ["strict", "check", "1001", "--phase", "pr"]
    rc, out = _cli(mod, capsys, *argv, "--base", "origin/trunk")
    assert (rc, out["mode"], out["ok"], out["findings"]) == (0, "strict", True, []) and fake["c"].config.mode == "strict"
    rc, out = _cli(mod, capsys, *argv, "--base", _full(tmp_path, s.base))  # a commit id: no ref, no target config here
    assert out["mode"] == "advisory"
    rc, out = _cli(mod, capsys, "strict", "check", "1001", "--phase", "done")  # no --base: nothing changes
    assert out["mode"] == "advisory"


def test_strict_check_with_a_bad_base_fails_closed(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)  # the PR's own config is strict
    assert mod.main(["strict", "check", "1001", "--phase", "pr", "--base", "nope"]) == 2  # no head config can wave an unknown target through
    assert "fake" not in capsys.readouterr().out


def test_head_cannot_repoint_test_command(tmp_path, monkeypatch, capsys, git_env):
    lax = "grep -q 'x = 1' src/app.py"  # passes at the red commit: the PR's own command would call red a pass
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    (tmp_path / "ado.config.json").write_text(json.dumps({**STRICT, "strict": {"test_command": lax}}))
    _target(tmp_path, {**STRICT, "strict": {"test_command": CMD}}, _full(tmp_path, s.base))
    rc, out = _cli(mod, capsys, "strict", "verify", "1001")  # the PR's config: its own command, red passes
    assert rc == 2 and "red-passes" in [f["rule"] for f in out["findings"]]
    rc, out = _cli(mod, capsys, "strict", "verify", "1001", "--base", "origin/trunk")
    assert rc == 0 and out["recorded"] is True
    assert f"command-sha256: {hashlib.sha256(CMD.encode()).hexdigest()[:12]}" in LEDGER(tmp_path).read_text()
    assert fake["c"].config.strict["test_command"] == CMD


def test_head_gate_tests_glob_cannot_make_every_file_a_test(tmp_path, monkeypatch, capsys, git_env):
    repo = _repo(tmp_path, git_env)
    base = git(repo, "rev-parse", "HEAD")
    red, green = _pair(repo, red_files={"tests/test_a.py": "def test_a():\n    assert 0\n", "src/other.py": "y = 1\n"})  # red touches code
    client = _project(tmp_path, ledger=_ledger_rows(("AC-1", RED_NOTE.format(red, green))), test_command=CMD)
    head = {**STRICT, "strict": {"test_command": CMD}, "gate": {"tests": {"**": "."}}}  # every file is a test
    (tmp_path / "ado.config.json").write_text(json.dumps(head))
    _target(tmp_path, {**STRICT, "strict": {"test_command": CMD}}, base)  # the target keeps the default globs
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr(m, "AdoClient", lambda c: FakeClient(c))
    argv = ["strict", "check", "1001", "--phase", "pr"]
    _, out = _cli(m, capsys, *argv, "--base", _full(tmp_path, base))  # a commit id: no target config, the PR's own glob
    assert "red-touches-code" not in [f["rule"] for f in out["findings"]]
    rc, out = _cli(m, capsys, *argv, "--base", "origin/trunk")
    assert rc == 2 and "red-touches-code" in [f["rule"] for f in out["findings"]]


def test_strict_verify_base_cannot_be_shadowed_by_a_tag(tmp_path, monkeypatch, capsys, git_env):
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env)
    git(tmp_path, "update-ref", "refs/remotes/origin/trunk", s.green)  # the real base already holds red and green
    git(tmp_path, "tag", "origin/trunk", s.base)  # a tag named like it, before them
    rc, out = _cli(mod, capsys, "strict", "verify", "1001", "--no-record", "--base", "origin/trunk")
    assert rc == 2 and [f["rule"] for f in out["findings"]] == ["not-in-range", "not-in-range"]
    git(tmp_path, "update-ref", "refs/remotes/origin/trunk", s.base)
    assert _cli(mod, capsys, "strict", "verify", "1001", "--no-record", "--base", "origin/trunk")[0] == 0
    for base in ("v-none", "", "refs/tags/origin/trunk"):  # nothing that is a tag or unknown stands in for the base
        assert _cli(mod, capsys, "strict", "verify", "1001", "--no-record", "--base", base)[0] == 2
