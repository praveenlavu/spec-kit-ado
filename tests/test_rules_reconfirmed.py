# P3-10b: `rules.reconfirmed`: the ledger's `Rules reviewed:` against the TARGET branch's copy of the ledger (real temp git repos, as test_ci_trust).
# A new id may be confirmed without a terminal, so an agent could delete the line and confirm again: the target's copy shows it.
import json
import subprocess

import pytest

from ado import gate, rules, strict
from ado.__main__ import main
from ado.dod import SECTION2
from tests.conftest import git
from tests.test_ci_trust import ORG, STRICT, _gate, _gate_pr, _md, _target
from tests.test_strict import LEDGER, RREV, _pr, _rstory

TARGET_CFG = {**STRICT, "rules": {"enabled": True}}
A, B, C = "aaaaaaaa", "bbbbbbbb", "cccccccc"
SECRET = "SECRET-MARK"


def _ledger(reviewed=None, frm="none", extra=""):
    """A ledger: header (`Rules from:`, and `Rules reviewed:` when `reviewed` is a string), then `extra` text, then section 2 onwards."""
    lines = f"- **Rules from:** {frm}\n" + ("" if reviewed is None else f"- **Rules reviewed:** {reviewed}\n")
    return f"# DoD — 1001\n\n{lines}{extra}\n{SECTION2}\n\n## 3. AC\n| AC | x | tests | ev | status |\n|---|---|---|---|---|\n| AC-1 | x | Gone.testX | | red |\n"


def _setup(tmp_path, monkeypatch, git_env, target, head, cfg=TARGET_CFG, head_cfg=ORG):
    """origin/main = a commit holding the ledger `target` (None: no ledger there) and `cfg`; the checked-out branch holds the ledger `head`."""
    return _gate_pr(tmp_path, monkeypatch, git_env, cfg, head_cfg,
                    target_files={"dod/DoD_1001.md": target} if target is not None else None, head_files={"dod/DoD_1001.md": head})


def _run(capsys, summary, *extra):
    return _gate(capsys, summary, "--base", "origin/main", *extra)


def test_deleted_reviewed_line_is_reconfirmed_all_ids(tmp_path, monkeypatch, capsys, git_env):
    summary = _setup(tmp_path, monkeypatch, git_env, _ledger(f"BR-1201-2@{B}, BR-1201-10@{C}, BR-1201-1@{A}"), _ledger())
    rc, out = _run(capsys, summary)
    assert out["rules"]["reconfirmed"] == [{"id": "BR-1201-1", "old": A, "new": None}, {"id": "BR-1201-2", "old": B, "new": None},
                                           {"id": "BR-1201-10", "old": C, "new": None}]  # (work item, number) order
    assert "rules reconfirmed: BR-1201-1 (aaaaaaaa -> none), BR-1201-2 (bbbbbbbb -> none)" in _md(summary)


def test_changed_hash_is_reconfirmed_with_old_and_new(tmp_path, monkeypatch, capsys, git_env):
    summary = _setup(tmp_path, monkeypatch, git_env, _ledger(f"BR-1201-1@{A}, BR-1201-2@{B}"), _ledger(f"BR-1201-1@{C}, BR-1201-2@{B}"))
    rc, out = _run(capsys, summary)
    assert out["rules"]["reconfirmed"] == [{"id": "BR-1201-1", "old": A, "new": C}]  # BR-1201-2 is unchanged
    assert "rules reconfirmed: BR-1201-1 (aaaaaaaa -> cccccccc)" in _md(summary) and "BR-1201-2" not in _md(summary).split("reconfirmed")[1]


def test_new_id_is_not_reconfirmed(tmp_path, monkeypatch, capsys, git_env):
    summary = _setup(tmp_path, monkeypatch, git_env, _ledger(f"BR-1201-1@{A}"), _ledger(f"BR-1201-1@{A}, BR-1201-2@{B}"))
    rc, out = _run(capsys, summary)
    assert out["rules"]["reconfirmed"] == [] and "reconfirmed" not in _md(summary)  # an empty list prints nothing


@pytest.mark.parametrize("target", [_ledger(), _ledger("none"), None], ids=["no-line", "none", "no-ledger"])  # no line / `none` / no ledger on the target
def test_target_without_line_is_empty(tmp_path, monkeypatch, capsys, git_env, target):
    summary = _setup(tmp_path, monkeypatch, git_env, target, _ledger(f"BR-1201-1@{A}"))
    rc, out = _run(capsys, summary)
    assert out["rules"]["reconfirmed"] == [] and "reconfirmed_note" not in out["rules"]


UNTRUSTED = {
    "comment-hides-the-first-heading": "# DoD\n\n- **Rules reviewed:** BR-1201-1@aaaaaaaa\n\n<!--\n## 1. Story\n-->\n" + SECTION2 + "\n",
    "open-fence-in-the-header": "# DoD\n\n```\n- **Rules reviewed:** BR-1201-1@aaaaaaaa\n\n## 1. Story\n",
    "duplicate-id": "# DoD\n\n- **Rules reviewed:** BR-1201-1@aaaaaaaa, BR-1201-1@bbbbbbbb\n\n" + SECTION2 + "\n",
    "second-line": "# DoD\n\n- **Rules reviewed:** BR-1201-1@aaaaaaaa\n- **Rules reviewed:** BR-1201-2@bbbbbbbb\n\n" + SECTION2 + "\n",
    "hostile-value": f"# DoD\n\n- **Rules reviewed:** BR-1201-1@aaaaaaaa, {SECRET}\n\n" + SECTION2 + "\n",
}


@pytest.mark.parametrize("name", UNTRUSTED)
def test_untrusted_target_header_is_empty_not_a_pass_and_not_a_crash(tmp_path, monkeypatch, capsys, git_env, name):
    summary = _setup(tmp_path, monkeypatch, git_env, UNTRUSTED[name], _ledger(f"BR-1201-1@{C}"))
    rc, out = _run(capsys, summary)
    assert rc in (0, 1) and out["rules"]["reconfirmed"] == [], name  # nothing recorded: not read as a record, not a traceback
    assert SECRET not in json.dumps(out) + _md(summary)


def _unreadable(capsys, summary, tmp_path):
    rc, out = _run(capsys, summary)
    assert rc in (0, 1) and "reconfirmed" not in out["rules"]  # omitted: neither [] (a pass) nor a guess
    assert out["rules"]["reconfirmed_note"] == rules.RECONFIRM_UNREADABLE == "rules reconfirmed: target ledger unreadable"
    assert rules.RECONFIRM_UNREADABLE in _md(summary)  # never silent


def test_unreadable_target_ledger_warns(tmp_path, monkeypatch, capsys, git_env):
    summary = _setup(tmp_path, monkeypatch, git_env, _ledger(f"BR-1201-1@{A}"), _ledger())
    parent = git(tmp_path, "rev-parse", "main")
    for files in ({"dod/DoD_1001.md": ("120000", "elsewhere")},  # a symlink
                  {"dod/DoD_1001.md": "x" * ((1 << 20) + 1)}):  # over 1 MiB
        _target(tmp_path, None, parent, ref="refs/remotes/origin/main", files={"ado.config.json": json.dumps(TARGET_CFG), **files})
        _unreadable(capsys, summary, tmp_path)
    # git failing, a file that is not UTF-8, a dod_path that points outside the repository
    real = strict._blob_at

    def patched(repo, commit, path, cap, label, bad=b"\xff\xfe# not utf-8"):  # only the ledger read is broken: the config read must still work
        if path.endswith("DoD_1001.md"):
            if bad is None:
                raise strict.ConfigInvalid("the story ledger at the target branch: git could not read the tree")
            return bad
        return real(repo, commit, path, cap, label)
    monkeypatch.setattr(strict, "_blob_at", patched)
    _unreadable(capsys, summary, tmp_path)
    monkeypatch.setattr(strict, "_blob_at", lambda *a: patched(*a, bad=None))
    _unreadable(capsys, summary, tmp_path)
    monkeypatch.undo()
    for bad in ("../elsewhere", "/etc", ":(glob)*", "dod/../../x"):
        assert strict.target_reviewed(bad, str(tmp_path), "origin/main", 1001) is None, bad
    assert strict.target_reviewed("dod", str(tmp_path), "origin/nope", 1001) is None  # a base that is no commit here


def test_no_base_means_no_key(tmp_path, monkeypatch, capsys, git_env):
    summary = _setup(tmp_path, monkeypatch, git_env, _ledger(f"BR-1201-1@{A}"), _ledger(), head_cfg={**ORG, "rules": {"enabled": True}})
    monkeypatch.setattr(strict, "target_reviewed", lambda *a: pytest.fail("the target is read only with --base"))
    rc, out = _gate(capsys, summary)  # rules on (the PR's own config), no --base
    assert "rules" in out and "reconfirmed" not in out["rules"] and "reconfirmed_note" not in out["rules"]
    assert "reconfirmed" not in _md(summary)


def test_off_is_byte_identical(tmp_path, monkeypatch, capsys, git_env):
    """Rules absent or disabled: with --base the output is the same whatever the target ledger holds, and the target ledger is never read."""
    for n, cfg in enumerate(({**STRICT}, {**STRICT, "rules": {"enabled": False}})):
        repo = tmp_path / f"r{n}"
        repo.mkdir()
        summary = _setup(repo, monkeypatch, git_env, _ledger(f"BR-1201-1@{A}"), _ledger(), cfg=cfg)
        monkeypatch.setattr(strict, "target_reviewed", lambda *a: pytest.fail("rules are off: the target ledger is not read"))
        rc1, out1 = _run(capsys, summary)
        md1 = _md(summary)
        _target(repo, None, git(repo, "rev-parse", "main"), ref="refs/remotes/origin/main",
                files={"ado.config.json": json.dumps(cfg), "dod/DoD_1001.md": _ledger(f"BR-1201-1@{B}")})
        rc2, out2 = _run(capsys, summary)
        assert (rc1, out1, md1) == (rc2, out2, _md(summary)) and "rules" not in out1 and "reconfirmed" not in md1 + json.dumps(out1)
        monkeypatch.undo()


def test_warning_not_finding_exit_code_unchanged(tmp_path, monkeypatch, capsys, git_env):
    summary = _setup(tmp_path, monkeypatch, git_env, _ledger(f"BR-1201-1@{A}"), _ledger(f"BR-1201-1@{A}"))
    rc, out = _run(capsys, summary)
    repo2 = tmp_path / "again"
    repo2.mkdir()
    summary2 = _setup(repo2, monkeypatch, git_env, _ledger(f"BR-1201-1@{A}"), _ledger())  # the same, but the PR deleted the line
    rc2, out2 = _run(capsys, summary2)
    assert out["rules"]["reconfirmed"] == [] and len(out2["rules"]["reconfirmed"]) == 1
    assert (rc, out["blocked_by"], out["blocking"]) == (rc2, out2["blocked_by"], out2["blocking"])  # a note: no new block, no exit-code change
    assert "rules_reconfirmed" not in out2["blocked_by"] and "rules_reconfirmed" not in gate.GATE_CHECKS
    assert "Blocking" not in _md(summary2).split("reconfirmed")[0].split("Advisory")[-1] and "**Advisory:**" in _md(summary2)


def test_no_rule_text_in_output(tmp_path, monkeypatch, capsys, git_env):
    body = f"- **Spec dir:** {SECRET}-spec\nThe rule text {SECRET} is here\n"
    summary = _setup(tmp_path, monkeypatch, git_env, _ledger(f"BR-1201-1@{A}", extra=body), _ledger(f"BR-1201-1@{C}", extra=body))
    rc, out = _run(capsys, summary)
    cap = capsys.readouterr()
    assert out["rules"]["reconfirmed"] == [{"id": "BR-1201-1", "old": A, "new": C}]
    assert SECRET not in json.dumps(out) + _md(summary) + cap.out + cap.err


# ---- strict check: a warning, never a finding ----
def _tgt(root, ledger, ref="refs/remotes/origin/trunk"):
    _target(root, None, git(root, "rev-parse", "HEAD"), ref=ref, files={"dod/DoD_1001.md": ledger})


def _check(client, root, phase, target="origin/trunk"):
    """strict.check against `target` with the adoption note of a non-strict target (an unrelated warning) taken out of `warnings`."""
    res = strict.check(client, 1001, phase, repo=str(root), target=target)
    warnings = [w for w in res.get("warnings", []) if w.startswith("rules reconfirmed:")]
    return {**{k: v for k, v in res.items() if k != "warnings"}, **({"warnings": warnings} if warnings else {})}


def _head_ledger(root):
    return LEDGER(root).read_text(encoding="utf-8")


@pytest.mark.parametrize("phase", ["pr", "done"])
def test_strict_check_warns_with_the_same_ids_and_changes_nothing_else(tmp_path, git_env, phase):
    root, client = _rstory(tmp_path, git_env)
    base = _pr(root, client, phase)  # no target: the baseline
    assert base == {"phase": phase, "mode": "strict", "ok": True, "findings": [], "overrides_used": []}
    head = _head_ledger(root)
    _tgt(root, head.replace(RREV, "BR-1201-1@" + A + ", BR-1201-2@" + B))  # the target recorded other hashes
    res = _check(client, root, phase)
    assert {k: v for k, v in res.items() if k != "warnings"} == base  # a warning: ok, findings and overrides are unchanged
    [w] = [w for w in res["warnings"] if w.startswith("rules reconfirmed: ")]
    assert f"BR-1201-1 ({A} -> " in w and f"BR-1201-2 ({B} -> " in w and w.endswith("re-confirmed without the target branch's recorded hash: check the PR diff")
    _tgt(root, head)  # the same record: nothing
    assert _check(client, root, phase) == base
    _tgt(root, head.replace(RREV, "BR-1201-1@" + A))  # BR-1201-2 is new at the head
    assert "BR-1201-2" not in " ".join(_check(client, root, phase).get("warnings", []))
    assert client.calls == []  # local git only


def test_strict_check_unreadable_target_ledger_warns_never_finds(tmp_path, git_env, monkeypatch):
    root, client = _rstory(tmp_path, git_env)
    _target(root, None, git(root, "rev-parse", "HEAD"), ref="refs/remotes/origin/trunk", files={"dod/DoD_1001.md": ("120000", "elsewhere")})
    res = _check(client, root, "pr")
    assert res["ok"] is True and res["findings"] == [] and res["warnings"] == [rules.RECONFIRM_UNREADABLE]


def test_strict_check_rules_off_or_no_target_is_the_baseline(tmp_path, git_env, monkeypatch):
    root, client = _rstory(tmp_path, git_env, enabled=False)
    _tgt(root, _head_ledger(root).replace(RREV, "BR-1201-1@" + A))
    monkeypatch.setattr(strict, "target_reviewed", lambda *a: pytest.fail("not read"))
    res = _check(client, root, "pr")
    assert res == {"phase": "pr", "mode": "strict", "ok": True, "findings": [], "overrides_used": []}
    root2, client2 = _rstory(tmp_path, git_env, name="t")
    assert strict.check(client2, 1001, "pr", repo=str(root2)) == {"phase": "pr", "mode": "strict", "ok": True, "findings": [], "overrides_used": []}


def test_the_pure_comparison():
    old = {"BR-1201-1": A, "BR-1201-2": B, "BR-9-1": C}
    assert rules.reconfirmed(old, {"BR-1201-1": A, "BR-1201-2": C, "BR-1201-3": B}) == [
        {"id": "BR-9-1", "old": C, "new": None}, {"id": "BR-1201-2", "old": B, "new": C}]
    assert rules.reconfirmed({}, {"BR-1201-1": A}) == [] and rules.reconfirmed(old, old) == []
    many = {f"BR-5-{n}": A for n in range(1, 31)}
    text = rules.reconfirmed_text(rules.reconfirmed(many, {}))
    assert text.count(" -> none") == 20 and text.endswith("and 10 more")
