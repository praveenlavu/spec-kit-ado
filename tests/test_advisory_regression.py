"""Advisory regression (P2-15): on a project with no `ado.stack.json` and no lockfile, every verb that existed in piece 1's baseline
(main a0b3c9e) prints the same stdout, the same stderr and exits with the same code as it did there.

The baseline `src/` is exported with `git archive` into a temp dir (no worktree metadata is touched, so parallel test workers are safe) and
both trees run the same verbs (the only normalised difference: the top-level argparse usage line names the new `kits` and `stack` verbs), in the same fixture path, with the venv's bin directory hidden from PATH. Skipped when the baseline commit is
not in this clone (a shallow CI checkout)."""
import os
import shutil
import subprocess
import sys
import tarfile
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASELINE = "a0b3c9e"
LEDGER = """# DoD — 1001

- **Spec dir:** specs/001-x

## 1. Story

### Acceptance criteria
1. A member count is shown.

## 2. Brainstorm & plan

- Approach: count active members.
- [ ] Q1: Which states count? → _(open)_ · owner: PO

## 3. AC → test traceability

| AC | Test | Status |
|----|------|--------|
| AC-1 | tests/test_app.py::test_count | pending |

## 4. Gate & review log  (appended by speckit.ado.gate and speckit.ado.review)

## 5. Checklist
- [ ] x
"""
CONFIG = ('{"organization": "contoso", "project": "Fabrikam", "api_version": "7.1", "dod_path": "dod", "gate": {"tests": {}, "block": []},'
          ' "attachments_allow": [".md", ".svg", ".txt"], "review_block_on": [], "active_state": "Active", "task_type": "Task",'
          ' "repo_host": "github", "repository": "", "branch_prefix": "feature/", "stack": {"kit": "", "phases": {}}}\n')
# every verb of the baseline CLI (setup is interactive and left out); network verbs stop at "no token" before any request
VERBS = [
    ["--version"], ["strict", "mode"], ["strict", "mode", "--base", "origin/main"],
    ["strict", "check", "1001", "--phase", "implement"], ["strict", "check", "1001", "--phase", "pr"], ["strict", "check", "1001", "--phase", "done"],
    ["strict", "verify", "1001"], ["strict", "override", "1001", "test-command", "--reason", "twenty characters or more here"],
    ["gate", "1001", "--offline"], ["gate", "1001", "--offline", "--changed"], ["gate", "1001", "--offline", "--base", "main"],
    ["questions", "1001"], ["decisions", "1001"],
    ["decisions", "1001", "--add", "Use the nightly build", "--kind", "decision", "--why", "fixture"], ["decisions", "1001"],
    ["decisions", "1001", "--post"], ["questions", "1001", "--post"],
    ["branch", "--id-from", "feature/ab1001-count-active-members"], ["branch", "--id-from", "main"],
    ["dod", "1001"], ["dod", "1001", "--refresh"], ["done", "1001"], ["push", "1001"], ["branch", "1001"], ["branch", "1001", "--link"],
    ["pr", "create", "1001"], ["pr", "create", "1001", "--title", "x", "--body-file", "dod/DoD_1001.md", "--base", "main", "--head", "main"],
    ["pr", "comment", "1", "--body-file", "dod/DoD_1001.md"],
    ["get", "1001"], ["query", "SELECT [System.Id] FROM WorkItems"], ["create", "--type", "Task", "--title", "x"], ["update", "1001", "--state", "Active"],
    ["comment", "1001", "x"], ["comments", "1001"], ["link", "1001", "--to", "1002"], ["attach", "1001", "dod/DoD_1001.md"],
    ["attachments", "1001"],
]
HELPS = [[v, "--help"] for v in ("get", "query", "create", "update", "comment", "comments", "link", "attach", "attachments", "branch", "dod",
                                 "questions", "decisions", "gate", "push", "done")] + [["pr", "--help"], ["strict", "--help"], ["strict", "check", "--help"],
                                 ["strict", "verify", "--help"], ["strict", "override", "--help"], ["strict", "mode", "--help"]]


def _have_baseline():
    r = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{BASELINE}^{{commit}}"], capture_output=True)
    return r.returncode == 0


def _env(home, src):
    path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep) if ".venv" not in p and "/venv/" not in p)
    return {"PATH": path, "HOME": str(home), "PYTHONPATH": str(src), "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Dana Fictional", "GIT_COMMITTER_NAME": "Dana Fictional",
            "GIT_AUTHOR_EMAIL": "dana@example.invalid", "GIT_COMMITTER_EMAIL": "dana@example.invalid",
            "GIT_AUTHOR_DATE": "2026-10-01T10:00:00+00:00", "GIT_COMMITTER_DATE": "2026-10-01T10:00:00+00:00", "TERM": "dumb"}


def _git(proj, env, *a):
    subprocess.run(["git", "-C", str(proj), *a], check=True, capture_output=True, env=env)


def _fixture(proj, env):
    """A fresh advisory project: no ado.stack.json, no ado.kits.lock.json, two commits, a ledger and a spec dir. Deterministic."""
    shutil.rmtree(proj, ignore_errors=True)
    proj.mkdir(parents=True)
    _git(proj, env, "init", "-q", "-b", "main")
    files = {"ado.config.json": CONFIG, "dod/DoD_1001.md": LEDGER, "specs/001-x/spec.md": "# Spec\n", "specs/001-x/plan.md": "# Plan\n",
             "specs/001-x/tasks.md": "- [ ] T001 Count members\n", "src/app.py": "def count():\n    return 0\n",
             "tests/test_app.py": "from src.app import count\n\n\ndef test_count():\n    assert count() == 0\n"}
    for rel, text in files.items():
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (proj / rel).write_text(text)
    _git(proj, env, "add", "-A")
    _git(proj, env, "commit", "-q", "-m", "AB#1001 first")
    (proj / "src/app.py").write_text("def count():\n    return 1\n")
    _git(proj, env, "add", "-A")
    _git(proj, env, "commit", "-q", "-m", "AB#1001 second")
    assert not (proj / "ado.stack.json").exists() and not (proj / "ado.kits.lock.json").exists()


def _run_all(src, proj, home, argvs):
    env = _env(home, src)
    out = subprocess.run([sys.executable, "-c", "import ado; print(ado.__file__)"], capture_output=True, text=True, env=env, cwd=str(proj.parent))
    assert out.stdout.strip().startswith(str(src)), out.stdout + out.stderr  # the tree under test, not an installed copy
    _fixture(proj, env)
    results = []
    for argv in argvs:
        r = subprocess.run([sys.executable, "-m", "ado", *argv], capture_output=True, text=True, env=env, cwd=str(proj), stdin=subprocess.DEVNULL, timeout=120)
        results.append((argv, r.returncode, r.stdout, r.stderr))
    results.append((["<files>"], 0, "\n".join(sorted(str(p.relative_to(proj)) for p in proj.rglob("*") if ".git" not in p.parts and p.is_file())), ""))
    results.append((["<ledger>"], 0, (proj / "dod/DoD_1001.md").read_text(), ""))
    results.append((["<decisions>"], 0, (proj / "dod/Decisions_1001.md").read_text() if (proj / "dod/Decisions_1001.md").exists() else "", ""))
    return results


def compare(tmp_path):
    """Returns (verbs compared, list of differences) between the baseline export and the working tree."""
    tar = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", BASELINE, "src"], check=True, capture_output=True).stdout
    base = tmp_path / "baseline"
    base.mkdir()
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(base)
    argvs = VERBS + HELPS
    proj, home = tmp_path / "proj", tmp_path / "home"
    home.mkdir()
    old = _run_all(base / "src", proj, home, argvs)
    new = _run_all(ROOT / "src", proj, home, argvs)
    # The listed additions: argparse's top-level usage line names the new verbs.
    # (and the verbs `doctor` and `status`, next to `setup`); any later verb belongs here too, so the usage line cannot diverge silently
    def usage(t):
        return t.replace("pr,kits,stack,strict", "pr,strict").replace("setup,doctor,status,get", "setup,get")
    def comments_help(t):
        """P4-7b adds `--author-ids` to `comments`. Remove exactly what it adds: its usage token, its two help lines and the two spaces by
        which argparse widens the option column (the `-h` and `--out` lines). Everything else is still compared byte for byte."""
        return (t.replace(" [--author-ids]", "").replace("  -h, --help    show", "  -h, --help  show").replace("  --out PATH    markdown", "  --out PATH  markdown")
                .replace("  --author-ids  print `date | author id | first 40 scrubbed characters` per\n                comment (to find an approver's identity id); writes nothing\n", ""))
    def decisions_help(t):
        """P4-9 adds `--sync` to `decisions`. Remove exactly what it adds: its usage token (argparse re-wraps the two usage lines it sits in) and its four help
        lines. Everything else is still compared byte for byte."""
        t = (t.replace(" [--sync] [--if-comment-mode]\n", " [--sync]\n")  # P4-14 adds `--if-comment-mode` after `--sync`: remove its usage token and its five help lines
             .replace("  --if-comment-mode   with --sync: exit 0 with a note, before any token or\n                      request, unless `approval` is \"comment\" on the target\n"
                      "                      branch; then also when the decisions file does not exist\n                      at all (any other refusal stays exit 2). The workflow's\n"
                      "                      sync step uses it, so a terminal-mode run is unchanged\n", ""))
        return (t.replace("[--resolve D<n>] [--force] [--sync]\n                     [--validated | --rejected] [--by BY] [--note NOTE]",
                          "[--resolve D<n>] [--force] [--validated | --rejected]\n                     [--by BY] [--note NOTE]")
                .replace("  --sync              record the approvers' `/approve D<n>` and `/reject D<n>`\n                      comments of the story as verdicts in the decisions file\n"
                         "                      (a local write, no ADO write; needs approvers on the\n                      target branch; with them, --resolve is refused)\n", ""))
    def questions_help(t):
        """P4-10 adds `--sync` to `questions`. Remove exactly what it adds: its usage token (argparse puts it at the end of the second usage line) and its five help lines.
        Everything else is still compared byte for byte."""
        t = (t.replace("[--sync] [--if-comment-mode]\n", "[--sync]\n")  # P4-14 adds `--if-comment-mode` after `--sync`: remove its usage token and its four help lines
             .replace("  --if-comment-mode   with --sync: exit 0 with a note, before any token or\n                      request, unless `approval` is \"comment\" on the target\n"
                      "                      branch (the workflow's sync step uses it, so a terminal-\n                      mode run is unchanged)\n", ""))
        return (t.replace("[--expect-plan PATH] [--sync]\n", "[--expect-plan PATH]\n")
                .replace("  --sync              record the approvers' `/answer Q<n>: text` comments of\n                      the story as the PO's answers in the ledger and list the\n"
                         "                      questions due under unattended.assume_after_hours (a\n                      local write, no ADO write; needs approvers on the target\n"
                         "                      branch)\n", ""))
    def done_help(t):
        """P4-11 adds `--base` to `done` (the pre-merge target the approvers are read from). Remove exactly what it adds: its usage token (argparse re-wraps the
        usage) and its three help lines. Everything else is still compared byte for byte."""
        return (t.replace("[--expect-plan PATH]\n                [--base REF]\n                id", "[--expect-plan PATH] id")
                .replace("  --base REF          CI: the pre-merge target branch (a full commit id or a\n                      ref) the approvers are read from, never the merged\n"
                         "                      commit's own config; default origin/HEAD\n", ""))
    new = [(a, rc, usage(o), usage(e)) for a, rc, o, e in new]
    new = [(a, rc, done_help(o), done_help(e)) if a[0] == "done" else (a, rc, o, e) for a, rc, o, e in new]
    new = [(a, rc, comments_help(o), e) if a == ["comments", "--help"] else (a, rc, o, e) for a, rc, o, e in new]
    new = [(a, rc, decisions_help(o), e) if a == ["decisions", "--help"] else (a, rc, o, e) for a, rc, o, e in new]
    new = [(a, rc, questions_help(o), e) if a == ["questions", "--help"] else (a, rc, o, e) for a, rc, o, e in new]
    diffs = [f"{a[0]}: rc {a[1]}->{b[1]}, stdout same {a[2] == b[2]}, stderr same {a[3] == b[3]}" for a, b in zip(old, new) if a != b]
    return len(argvs), diffs, old


@pytest.mark.skipif(not _have_baseline(), reason=f"baseline commit {BASELINE} is not in this clone")
def test_pre_existing_verbs_are_byte_identical_to_the_baseline_without_a_profile_or_lockfile(tmp_path):
    n, diffs, old = compare(tmp_path)
    assert n >= 50 and not diffs, "\n".join(diffs)
    # the comparison is not vacuous: the fixture ran real verbs (a gate summary, decisions, an advisory note), not only usage errors
    text = "\n".join(o[2] for o in old)
    assert '"mode": "advisory"' in text and "D1" in text and old[[a[0] for a in old].index(["gate", "1001", "--offline"])][1] in (0, 1)
