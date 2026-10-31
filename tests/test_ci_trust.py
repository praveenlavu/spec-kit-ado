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
from tests.test_stack_show import approve, profile
from tests.test_strict import CMD, LEDGER, MARK, RED_NOTE, STACK_CMD, _d, _kids, _ledger_rows, _pair, _project, _repo
from tests.test_strict_wire import _full, _wire

ORG = {"organization": "contoso", "project": "Fabrikam"}
STRICT = {**ORG, "mode": "strict"}
LINE = "config: target branch (strict)"


def _cfg_text(cfg):
    return cfg if isinstance(cfg, str) else json.dumps(cfg)


# ---- gate ----
def _gate_pr(tmp_path, monkeypatch, git_env, target, head, decisions_md=None, target_files=None, head_files=None):
    """main holds `target` (a dict, raw text or None) = refs/remotes/origin/main; the branch `feature` (checked out) holds `head` and
    a ledger with one AC whose test does not exist (AC traceability finds it) and, optionally, a decisions file."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "AB#1001 base", {"README.md": "x\n", **({"ado.config.json": _cfg_text(target)} if target is not None else {}),
                                      **(target_files or {})})
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    files = {"dod/DoD_1001.md": f"{SECTION2}\n## 3. AC\n| AC | x | tests | ev | status |\n|---|---|---|---|---|\n| AC-1 | x | Gone.testX | | red |\n",
             **(head_files or {})}
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
def _target(repo, cfg, parent, ref="refs/remotes/origin/trunk", files=None):
    """A commit with `ado.config.json` = cfg (and `files`: path -> text, or (mode, text); None removes it) on top of `parent` (not on the
    checked-out branch), set as `ref`. cfg None and no files: the parent as it is."""
    files = {**({"ado.config.json": _cfg_text(cfg)} if cfg is not None else {}), **(files or {})}
    if not files:
        git(repo, "update-ref", ref, parent)
        return
    env = {**os.environ, "GIT_INDEX_FILE": str(repo / ".git" / "trust-index")}

    def run(*a, inp=None):
        return subprocess.run(["git", "-C", str(repo), *a], env=env, input=inp, capture_output=True, text=True, check=True).stdout.strip()
    run("read-tree", parent)
    for path, text in files.items():
        if text is None:
            run("update-index", "--force-remove", path)
            continue
        mode, text = text if isinstance(text, tuple) else ("100644", text)
        blob = run("hash-object", "-w", "--stdin", inp=text)
        run("update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}")
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


# ---- V3-14a: `ado strict mode [--base REF]`: the mode CI would judge a PR by (read-only, no token, no network) ----
def _mode(monkeypatch, capsys, *extra):
    monkeypatch.setattr(m, "AdoClient", lambda c: (_ for _ in ()).throw(AssertionError("strict mode is local: no client")))
    rc = main(["strict", "mode", *extra])
    cap = capsys.readouterr()
    return rc, (json.loads(cap.out) if cap.out.strip() else None), cap.err


TARGET, PULL = "target branch", "pull request"


@pytest.mark.parametrize("target,head,mode,source", [
    (STRICT, ORG, "strict", TARGET),  # the PR says advisory: the target's strict config wins
    (STRICT, None, "strict", TARGET),  # a PR that deletes its config
    (STRICT, {**ORG, "mode": "advisory"}, "strict", TARGET),
    (None, ORG, "advisory", PULL),
    (ORG, ORG, "advisory", PULL),
    ({**ORG, "mode": "advisory", "gate": {"block": ["ac_traceability"]}}, ORG, "advisory", PULL),
    (None, STRICT, "strict", PULL),  # the first PR that introduces strict mode: its own config
    (ORG, STRICT, "strict", PULL),  # a PR that adds strictness to an advisory target
])
def test_strict_mode_says_which_config_judges_the_pr(tmp_path, monkeypatch, capsys, git_env, target, head, mode, source):
    _gate_pr(tmp_path, monkeypatch, git_env, target, head)
    rc, out, err = _mode(monkeypatch, capsys, "--base", "origin/main")
    assert (rc, out, err) == (0, {"mode": mode, "source": source}, "")


def test_strict_mode_without_base_is_the_pr_config_and_reads_no_ref(tmp_path, monkeypatch, capsys, git_env):
    _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG)
    monkeypatch.setattr(m.Config, "load_from_ref", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no --base: no ref is read")))
    assert _mode(monkeypatch, capsys)[:2] == (0, {"mode": "advisory", "source": PULL})


@pytest.mark.parametrize("target", ['{"organization": "contoso"}', "{not json", "[]", '{"organization": "contoso", "project": "p", "mode": "lax"}'])
def test_strict_mode_with_an_invalid_target_exit_3(tmp_path, monkeypatch, capsys, git_env, target):
    _gate_pr(tmp_path, monkeypatch, git_env, target, ORG)  # a valid head config does not rescue it
    rc, out, err = _mode(monkeypatch, capsys, "--base", "origin/main")
    assert rc == 3 and out is None and err.startswith("error: ") and err.count("\n") == 1
    assert str(tmp_path) not in err and "origin/main" not in err.replace("target branch", "")  # fixed text: no path, no ref


def test_strict_mode_with_an_invalid_head_config_exit_3(tmp_path, monkeypatch, capsys, git_env):
    _gate_pr(tmp_path, monkeypatch, git_env, None, '{"organization": "contoso", "project": "Fabrikam", "gate": {"block": ["nope"]}}')
    assert _mode(monkeypatch, capsys, "--base", "origin/main")[0] == 3
    assert _mode(monkeypatch, capsys)[0] == 3


@pytest.mark.parametrize("base", ["origin/nosuch", "nosuch", "abc", "--output=x", "origin/main:ado.config.json"])
def test_strict_mode_unresolvable_base_under_a_strict_pr_config_exit_3(tmp_path, monkeypatch, capsys, git_env, base):
    _gate_pr(tmp_path, monkeypatch, git_env, STRICT, STRICT)
    rc, out, err = _mode(monkeypatch, capsys, f"--base={base}")
    assert rc == 3 and out is None and err.startswith("error: ") and not (tmp_path / "x").exists()


@pytest.mark.parametrize("base", ["origin/nosuch", "HEAD~5", "nosuch", "--output=x"])
def test_strict_mode_unresolvable_base_with_an_advisory_pr_config_keeps_advisory(tmp_path, monkeypatch, capsys, git_env, base):
    _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG)  # the target is strict, but this base cannot be read here: the v0.2 path
    assert _mode(monkeypatch, capsys, f"--base={base}") == (0, {"mode": "advisory", "source": PULL}, "")


def test_strict_mode_without_any_config_is_a_plain_error_not_fail_closed(tmp_path, monkeypatch, capsys, git_env):
    _gate_pr(tmp_path, monkeypatch, git_env, None, None)  # no config anywhere: the templates treat this as advisory
    rc, out, err = _mode(monkeypatch, capsys, "--base", "origin/main")
    assert rc == 2 and out is None and err.startswith("error: ")


def test_strict_mode_a_tag_named_like_the_base_cannot_shadow_it(tmp_path, monkeypatch, capsys, git_env):
    _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG)
    git(tmp_path, "tag", "origin/main", "HEAD")  # a tag on the PR's own commit
    assert _mode(monkeypatch, capsys, "--base", "origin/main")[:2] == (0, {"mode": "strict", "source": TARGET})


def test_strict_mode_needs_no_token_and_makes_no_request(tmp_path, monkeypatch, capsys, git_env):
    _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG)
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no keychain, no stored token
    assert _mode(monkeypatch, capsys, "--base", "origin/main")[0] == 0


# ---- P2-5b: the stack profile and the kit lockfile come from the TARGET branch ----
NOTE = "trust-introducing PR: review ado.stack.json as code"
STACK_STRICT = {**STRICT, "strict": {"test_command": STACK_CMD}}
OVERSIZE = "{" + " " * (256 * 1024 + 1) + "}"
TOKEN = "gh" + "p_" + "a" * 36  # built here so this file holds no secret-looking literal
BAD_PROFILES = {
    "parse": "{not json " + MARK,
    "duplicate-key": '{"schema": 1, "schema": 1, "x": "' + MARK + '"}',
    "schema": json.dumps({**profile(), "version": MARK}),
    "unknown-key": json.dumps({**profile(), MARK: 1}),
    "secret": json.dumps({**profile(), "sources": {"S1": {**profile()["sources"]["S1"], "evidence": TOKEN + MARK}, "S2": profile()["sources"]["S2"]}}),
    "oversize": OVERSIZE,
}
BAD_LOCKS = {
    "parse": "{not json " + MARK,
    "duplicate-key": '{"schema": 1, "schema": 1, "kits": {}}',
    "schema": json.dumps({"schema": 2, "kits": {}, "x": MARK}),
    "not-object": "[]",
    "kit-id": json.dumps({"schema": 1, "kits": {"Bad Id " + MARK: {}}}),
    "secret": json.dumps({"schema": 1, "kits": {"sf": {"source": TOKEN + MARK}}}),
    "oversize": OVERSIZE,
}


def _all_output(capsys, summary=None):
    cap = capsys.readouterr()
    return cap.out + cap.err + (_md(summary) if summary and os.path.exists(summary) else "")


@pytest.mark.parametrize("raw", BAD_PROFILES.values(), ids=BAD_PROFILES)
def test_invalid_target_profile_gate_exit_3_check_exit_2(tmp_path, monkeypatch, capsys, git_env, raw):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG, target_files={"ado.stack.json": raw})
    assert _gate(capsys, summary, "--base", "origin/main")[0] == 3
    md = _md(summary)
    assert "ado.stack.json at the target branch" in md and MARK not in md and TOKEN not in md and "origin/main" not in md.replace("target branch", "")
    monkeypatch.setattr(m, "AdoClient", lambda c: FakeClient(c))
    capsys.readouterr()
    rc, out = _cli(m, capsys, "strict", "check", "1001", "--phase", "implement", "--base", "origin/main")
    err = _all_output(capsys)
    assert (rc, out) == (2, None)  # an error: line on stderr, no JSON
    assert _cli(m, capsys, "strict", "verify", "1001", "--no-record", "--base", "origin/main")[0] == 2
    assert _cli(m, capsys, "strict", "mode", "--base", "origin/main")[0] == 3
    assert _cli(m, capsys, "branch", "--id-from", "feature/ab1001-x", "--base", "origin/main")[0] == 3
    assert MARK not in err and TOKEN not in err


@pytest.mark.parametrize("raw", BAD_LOCKS.values(), ids=BAD_LOCKS)
def test_invalid_target_lock_gate_exit_3(tmp_path, monkeypatch, capsys, git_env, raw):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG, target_files={"ado.kits.lock.json": raw})
    assert _gate(capsys, summary, "--base", "origin/main")[0] == 3
    md = _md(summary)
    assert "ado.kits.lock.json at the target branch" in md and MARK not in md and TOKEN not in md
    assert _cli(m, capsys, "strict", "mode", "--base", "origin/main")[0] == 3
    assert MARK not in _all_output(capsys)


def test_valid_target_profile_and_lock_do_not_fail_the_config(tmp_path, monkeypatch, capsys, git_env):
    files = {"ado.stack.json": json.dumps(approve(profile())), "ado.kits.lock.json": json.dumps({"schema": 1, "kits": {"sf": {"version": "1.0.0"}}})}
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG, target_files=files)
    rc, out = _gate(capsys, summary, "--base", "origin/main")
    assert rc == 1 and out["config"] == "target branch (strict)"  # judged strict by the target (AC traceability blocks), not exit 3
    assert _cli(m, capsys, "strict", "mode", "--base", "origin/main")[1] == {"mode": "strict", "source": "target branch"}


def test_target_blobs_are_read_through_blob_at_with_the_cap_and_the_labels(tmp_path, monkeypatch, capsys, git_env):
    _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG, target_files={"ado.stack.json": json.dumps(approve(profile()))})
    calls, real = [], strict._blob_at
    monkeypatch.setattr(strict, "_blob_at", lambda *a, **k: calls.append(a[2:]) or real(*a, **k))
    assert _cli(m, capsys, "strict", "mode", "--base", "origin/main")[0] == 0
    assert calls == [("ado.config.json", 1024 * 1024, "ado.config.json at the target branch"),
                     ("ado.stack.json", 256 * 1024, "ado.stack.json at the target branch"),
                     ("ado.kits.lock.json", 256 * 1024, "ado.kits.lock.json at the target branch")]


def test_advisory_or_absent_target_config_reads_no_stack_files(tmp_path, monkeypatch, capsys, git_env):
    for n, target in enumerate((None, ORG)):
        repo = tmp_path / f"r{n}"
        repo.mkdir()
        _gate_pr(repo, monkeypatch, git_env, target, ORG, target_files={"ado.stack.json": "{not json", "ado.kits.lock.json": "{not json"})
        seen = []
        real = strict._blob_at
        monkeypatch.setattr(strict, "_blob_at", lambda *a, **k: seen.append(a[2]) or real(*a, **k))
        assert _cli(m, capsys, "strict", "mode", "--base", "origin/main")[0] == 0
        assert "ado.stack.json" not in seen and "ado.kits.lock.json" not in seen  # v0.2: nothing new is read
        monkeypatch.undo()
        monkeypatch.chdir(repo)


# -- strict check: the target's profile judges --
def _ci(tmp_path, monkeypatch, git_env, target_files, head_files=None, target_cfg=STACK_STRICT, head_cfg=STACK_STRICT):
    """`origin/trunk` = base + the target's config and `target_files`; HEAD = base + the spec/ledger files, the PR's own config and
    `head_files`. Returns a runner for `strict check 1001 --phase implement --base origin/trunk` -> (rc, JSON, holder of the client)."""
    repo = _repo(tmp_path, git_env)
    base = git(repo, "rev-parse", "HEAD")
    _project(tmp_path, prof=None)
    _target(repo, target_cfg, base, files=target_files)
    mine = {k: v for k, v in (head_files or {}).items() if v is not None}
    inherited = {k: v for k, v in (target_files or {}).items() if isinstance(v, str) and k not in mine}  # the PR branched from the target
    commit(repo, "AB#1001 pr", {"ado.config.json": _cfg_text(head_cfg), **inherited, **mine})
    for gone in (k for k, v in (head_files or {}).items() if v is None):  # a head file of None: the PR deletes it
        git(repo, "rm", "-q", gone)
        git(repo, "commit", "-q", "-m", "AB#1001 delete")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setenv("ADO_TOKEN", "t")
    holder = {}

    def make(c):
        holder["c"] = FakeClient(c, responses=_kids("T001", "T002"))
        return holder["c"]
    monkeypatch.setattr(m, "AdoClient", make)
    return holder


def _run(capsys, *extra, phase="implement"):
    rc = main(["strict", "check", "1001", "--phase", phase, "--base", "origin/trunk", *extra])
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def _rules_of(out):
    return [f["rule"] for f in out["findings"]]


def test_target_profile_used_when_target_strict(tmp_path, monkeypatch, capsys, git_env, real_detect):
    holder = _ci(tmp_path, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(profile()))})
    rc, out = _run(capsys)  # the PR touches neither file: the target's approved profile judges, no note
    assert (rc, out["ok"], out["findings"]) == (0, True, []) and "warnings" not in out and holder["c"].config.from_target is True
    d = tmp_path / "rev"
    d.mkdir()
    _ci(d, monkeypatch, git_env, {"ado.stack.json": json.dumps(profile())}, head_files={"ado.stack.json": json.dumps(approve(profile()))})
    rc, out = _run(capsys)  # the PR approves its own profile: the target's draft is what counts (and the head copy is consistent)
    assert rc == 2 and _rules_of(out) == ["stack-profile-draft"] and "at the head commit" not in out["findings"][0]["detail"]


def test_head_draft_over_an_approved_target_is_refused_at_the_head_commit(tmp_path, monkeypatch, capsys, git_env, real_detect):
    """Fix round 1 (this replaces the P2-5b brief's 'head draft + target approved -> pass'): the target's verdict stays, and a head copy
    that would lock every later PR once merged is refused too."""
    _ci(tmp_path, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(profile()))}, head_files={"ado.stack.json": json.dumps(profile())})
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == ["stack-profile-draft"] and "at the head commit" in out["findings"][0]["detail"]
    assert out["warnings"] == [NOTE]


HEAD_BAD = {"draft": (lambda: profile(), "stack-profile-draft"),
            "changed": (lambda: approve(profile(), sha256="1" * 64), "stack-profile-changed"),
            "parse": (lambda: "{not json " + MARK, "stack-profile-invalid"),
            "unknown-key": (lambda: json.dumps({**profile(), MARK: 1}), "stack-profile-invalid")}


@pytest.mark.parametrize("kind", HEAD_BAD)
@pytest.mark.parametrize("bootstrap", [False, True], ids=["target-has-profile", "bootstrap"])
def test_head_profile_that_would_lock_strict_after_merge_is_refused(tmp_path, monkeypatch, capsys, git_env, real_detect, kind, bootstrap):
    make, rule = HEAD_BAD[kind]
    raw = make()
    _ci(tmp_path, monkeypatch, git_env, None if bootstrap else {"ado.stack.json": json.dumps(approve(profile()))},
        head_files={"ado.stack.json": raw if isinstance(raw, str) else json.dumps(raw)})
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == [rule] and "at the head commit" in out["findings"][0]["detail"] and out["warnings"] == [NOTE]
    assert MARK not in json.dumps(out)


def test_head_hand_edited_test_command_is_refused(tmp_path, monkeypatch, capsys, git_env, real_detect):
    _ci(tmp_path, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(profile()))}, head_cfg={**STRICT, "strict": {"test_command": "true"}})
    rc, out = _run(capsys)  # the head's profile is unchanged but its config no longer matches it: merged, every later PR would drift
    assert rc == 2 and _rules_of(out) == ["stack-profile-drift"] and "at the head commit" in out["findings"][0]["detail"] and out["warnings"] == [NOTE]
    d = tmp_path / "unset"
    d.mkdir()
    _ci(d, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(profile()))}, head_cfg=STRICT)  # the PR drops the command
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == ["stack-profile-drift"]


def test_head_deleting_the_profile_is_refused_and_a_null_test_is_consistent(tmp_path, monkeypatch, capsys, git_env, real_detect):
    _ci(tmp_path, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(profile()))}, head_files={"ado.stack.json": None})
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == ["stack-profile-missing"] and "at the head commit" in out["findings"][0]["detail"]
    null = profile()
    null["stacks"][0]["commands"]["test"] = {"run": None, "why": "tests run on the platform"}
    for n, boot in enumerate((False, True)):  # no command anywhere is consistent with a null test, in both flows
        d = tmp_path / f"n{n}"
        d.mkdir()
        _ci(d, monkeypatch, git_env, None if boot else {"ado.stack.json": json.dumps(approve(null))}, target_cfg=STRICT, head_cfg=STRICT,
            head_files={"ado.stack.json": json.dumps(approve({**null, "version": 3}))})
        rc, out = _run(capsys)
        assert _rules_of(out) == ["test-command"] and out["warnings"] == [NOTE], boot  # only the unset command (an l2 waiver covers it)


def test_the_note_fires_on_any_head_edit_of_the_trust_files(tmp_path, monkeypatch, capsys, git_env, real_detect):
    approved = json.dumps(approve(profile()))
    cases = {"lockfile": dict(head_files={"ado.kits.lock.json": json.dumps({"schema": 1, "kits": {}})}),
             "profile": dict(head_files={"ado.stack.json": json.dumps(approve({**profile(), "version": 3}))}),
             "command": dict(head_cfg={**STRICT, "strict": {"test_command": "true"}}, head_files={"ado.stack.json": json.dumps(
                 approve({**profile(), "stacks": [{**profile()["stacks"][0], "commands": {"test": {"run": "true", "source": "S1"}}}]}))})}
    for n, (name, kw) in enumerate(cases.items()):
        d = tmp_path / f"c{n}"
        d.mkdir()
        _ci(d, monkeypatch, git_env, {"ado.stack.json": approved}, **kw)
        rc, out = _run(capsys)
        assert (rc, out["ok"], out["warnings"]) == (0, True, [NOTE]), name  # consistent edits pass, flagged for review as code
    d = tmp_path / "none"
    d.mkdir()
    _ci(d, monkeypatch, git_env, {"ado.stack.json": approved})
    assert "warnings" not in _run(capsys)[1]


def test_head_edit_to_test_command_ignored_in_ci(tmp_path, monkeypatch, capsys, git_env, real_detect):
    other = profile()
    other["stacks"][0]["commands"]["test"]["run"] = "true"
    holder = _ci(tmp_path, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(profile()))},
                 head_files={"ado.stack.json": json.dumps(approve(other))},
                 head_cfg={**STRICT, "strict": {"test_command": "true"}})  # the PR re-points both, consistently
    rc, out = _run(capsys)
    assert (rc, out["ok"], out["findings"]) == (0, True, [])
    assert holder["c"].config.strict["test_command"] == STACK_CMD  # the command in force is the target's
    d = tmp_path / "drift"
    d.mkdir()
    _ci(d, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(profile()))}, target_cfg={**STRICT, "strict": {"test_command": "pytest -q"}},
        head_cfg={**STRICT, "strict": {"test_command": STACK_CMD}})  # the target's config drifted from its profile; the PR's own copy agrees
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == ["stack-profile-drift"]


def test_bootstrap_no_target_profile_is_note_not_block(tmp_path, monkeypatch, capsys, git_env, real_detect):
    _ci(tmp_path, monkeypatch, git_env, None)
    rc, out = _run(capsys)
    assert (rc, out["ok"], out["findings"]) == (0, True, []) and "warnings" not in out  # no profile anywhere: no finding, no note
    d = tmp_path / "ok"
    d.mkdir()
    _ci(d, monkeypatch, git_env, None, head_files={"ado.stack.json": json.dumps(approve(profile()))})
    rc, out = _run(capsys)  # the first profile: approved and consistent with the head's strict.test_command
    assert (rc, out["ok"], out["findings"], out["warnings"]) == (0, True, [], [NOTE])
    d = tmp_path / "drift"
    d.mkdir()
    _ci(d, monkeypatch, git_env, None, head_files={"ado.stack.json": json.dumps(approve(profile()))}, head_cfg={**STRICT, "strict": {"test_command": "true"}})
    rc, out = _run(capsys)  # approved but the head's command is not the composed one
    assert rc == 2 and _rules_of(out) == ["stack-profile-drift"] and out["warnings"] == [NOTE]


@pytest.mark.parametrize("raw", BAD_PROFILES.values(), ids=BAD_PROFILES)
def test_bootstrap_invalid_head_profile_refused(tmp_path, monkeypatch, capsys, git_env, real_detect, raw):
    _ci(tmp_path, monkeypatch, git_env, None, head_files={"ado.stack.json": raw})
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == ["stack-profile-invalid"] and out["warnings"] == [NOTE]
    assert MARK not in json.dumps(out) and TOKEN not in json.dumps(out)


def test_bootstrap_head_symlink_profile_refused(tmp_path, monkeypatch, capsys, git_env, real_detect):
    _ci(tmp_path, monkeypatch, git_env, None, head_files={"elsewhere.json": json.dumps(approve(profile()))})
    os.symlink("elsewhere.json", tmp_path / "ado.stack.json")
    commit(tmp_path, "AB#1001 link")
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == ["stack-profile-invalid"]


def test_bootstrap_applies_only_when_the_target_lacks_a_profile(tmp_path, monkeypatch, capsys, git_env, real_detect):
    _ci(tmp_path, monkeypatch, git_env, {"ado.stack.json": json.dumps(profile())}, head_files={"ado.stack.json": json.dumps(approve(profile()))})
    rc, out = _run(capsys)  # the target has a (draft) profile: it binds, and the head's approved copy is just flagged for review
    assert rc == 2 and _rules_of(out) == ["stack-profile-draft"] and out["warnings"] == [NOTE] and "at the head commit" not in out["findings"][0]["detail"]
    d = tmp_path / "local"
    d.mkdir()
    _ci(d, monkeypatch, git_env, None, target_cfg=None, head_cfg=STACK_STRICT)  # no target config: the PR's own strict config, a local one
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == ["stack-profile-missing"] and "warnings" not in out
    d = tmp_path / "plain"
    d.mkdir()
    _ci(d, monkeypatch, git_env, None, head_files={"ado.stack.json": json.dumps(approve(profile()))})
    rc, out = _run(capsys)  # and a target without a profile gives a note even when the head's profile is approved and complete
    assert rc == 0 and out["warnings"] == [NOTE]


def test_new_root_after_bootstrap_is_stack_unprofiled(tmp_path, monkeypatch, capsys, git_env, real_detect):
    _ci(tmp_path, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(profile()))}, head_files={"web/package.json": "{}\n"})
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == ["stack-unprofiled"] and "web" in out["findings"][0]["detail"]
    d = tmp_path / "covered"
    d.mkdir()
    web = profile()
    web["stacks"].append({"id": "web", "root": "web", "commands": {"test": {"run": None, "why": "none"}}})
    web = approve(web)
    _ci(d, monkeypatch, git_env, {"ado.stack.json": json.dumps(web)}, head_files={"web/package.json": "{}\n"})
    rc, out = _run(capsys)  # the profile on the target covers the root (the two-PR flow: profile first, then the root)
    assert "stack-unprofiled" not in _rules_of(out)
    e = tmp_path / "edit"
    e.mkdir()
    _ci(e, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(profile()))},
        head_files={"web/package.json": "{}\n", "ado.stack.json": json.dumps(web)})  # the PR adds the root AND its own profile edit: still refused
    rc, out = _run(capsys)
    assert rc == 2 and "stack-unprofiled" in _rules_of(out)


def test_a_new_stack_at_a_covered_root_is_unprofiled_by_family(tmp_path, monkeypatch, capsys, git_env, real_detect):
    _ci(tmp_path, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(profile()))},
        head_files={"package.json": "{}\n", "tsconfig.json": "{}\n"})  # a python profile at "."; the head adds a TypeScript project at the same root
    rc, out = _run(capsys)
    assert rc == 2 and _rules_of(out) == ["stack-unprofiled"] and "(typescript)" in out["findings"][0]["detail"]
    d = tmp_path / "fam"
    d.mkdir()
    ts = profile()
    ts["stacks"][0]["id"] = "typescript"  # the seed's id for a root detection calls node or typescript: one family
    _ci(d, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(ts))}, head_files={"package.json": "{}\n"})
    assert _rules_of(_run(capsys)[1]) == []
    e = tmp_path / "custom"
    e.mkdir()
    custom = profile()
    custom["stacks"][0]["id"] = "web-app"  # a free-form id covers its root, whatever is detected there
    _ci(e, monkeypatch, git_env, {"ado.stack.json": json.dumps(approve(custom))}, head_files={"package.json": "{}\n"})
    assert _rules_of(_run(capsys)[1]) == []


def test_stack_rules_bind_in_pr_and_done_phases_from_the_target(tmp_path, monkeypatch, capsys, git_env, real_detect):
    _ci(tmp_path, monkeypatch, git_env, {"ado.stack.json": json.dumps(profile())}, head_files={"ado.stack.json": json.dumps(approve(profile()))})
    for phase in ("pr", "done"):
        rc, out = _run(capsys, phase=phase)
        assert rc == 2 and "stack-profile-draft" in _rules_of(out), phase


# -- gate: the PR comment carries the note --
def test_gate_comment_notes_a_trust_introducing_pr(tmp_path, monkeypatch, capsys, git_env):
    summary = _gate_pr(tmp_path, monkeypatch, git_env, STRICT, ORG, head_files={"ado.stack.json": json.dumps(profile())})
    rc, out = _gate(capsys, summary, "--base", "origin/main")
    assert out["trust_note"] == NOTE and _md(summary).splitlines().count(NOTE) == 1
    cases = {"target has a profile": dict(target_files={"ado.stack.json": json.dumps(profile())}, head_files={"ado.stack.json": json.dumps(profile())}),
             "head adds none": {}, "advisory target": dict(head_files={"ado.stack.json": json.dumps(profile())})}
    for n, (name, kw) in enumerate(cases.items()):
        repo = tmp_path / f"c{n}"
        repo.mkdir()
        s = _gate_pr(repo, monkeypatch, git_env, ORG if name == "advisory target" else STRICT, ORG, **kw)
        rc, out = _gate(capsys, s, "--base", "origin/main")
        assert "trust_note" not in out and NOTE not in _md(s), name
    other = tmp_path / "nobase"
    other.mkdir()
    s = _gate_pr(other, monkeypatch, git_env, STRICT, ORG, head_files={"ado.stack.json": json.dumps(profile())})
    assert "trust_note" not in _gate(capsys, s)[1] and NOTE not in _md(s)  # no --base: nothing is judged from a target


def test_gate_comment_notes_any_head_edit_of_the_trust_files(tmp_path, monkeypatch, capsys, git_env):
    cases = {"lockfile": dict(head=ORG, head_files={"ado.kits.lock.json": json.dumps({"schema": 1, "kits": {}})}),
             "profile": dict(head=ORG, target_files={"ado.stack.json": json.dumps(approve(profile()))},
                             head_files={"ado.stack.json": json.dumps(approve({**profile(), "version": 3}))}),
             "command": dict(head={**ORG, "strict": {"test_command": "true"}})}
    for n, (name, kw) in enumerate(cases.items()):
        repo = tmp_path / f"e{n}"
        repo.mkdir()
        kw = {"head": ORG, **kw}
        summary = _gate_pr(repo, monkeypatch, git_env, STRICT, kw.pop("head"), **kw)
        rc, out = _gate(capsys, summary, "--base", "origin/main")
        assert out["trust_note"] == NOTE and _md(summary).splitlines().count(NOTE) == 1, name


def test_gate_comment_for_an_invalid_trust_file_does_not_blame_the_config(tmp_path, monkeypatch, capsys, git_env):
    for name, raw in (("ado.stack.json", "{not json"), ("ado.kits.lock.json", "[]")):
        repo = tmp_path / name
        repo.mkdir()
        summary = _gate_pr(repo, monkeypatch, git_env, STRICT, ORG, target_files={name: raw})
        assert _gate(capsys, summary, "--base", "origin/main")[0] == 3
        md = _md(summary)
        assert "target branch trust files are invalid:" in md and f"{name} at the target branch" in md and "ado.config.json is invalid" not in md
    other = tmp_path / "config"
    other.mkdir()
    summary = _gate_pr(other, monkeypatch, git_env, "{not json", ORG)  # a broken config file keeps its old wording
    assert _gate(capsys, summary, "--base", "origin/main")[0] == 3 and "ado.config.json is invalid" in _md(summary)
