#!/usr/bin/env python3
"""The local half of the release checklist's consumer cases (a) to (f) (docs/release-checklist.md, step 3).

Each case in cases.json is built as a throwaway git repository from the static files under fixtures/ (fictional names only), then the same
`ado` commands a CI template runs are executed in it, in this process, and the exit code and finding rule ids are compared with the case's
expectation. Nothing here talks to Azure DevOps or GitHub: the one client is `SmokeClient` below, which answers from fixtures/comments.json
and refuses (raises) any request it does not know. A pass here shows the checks give the expected answer on a fixture; it does not show
that a hosted runner, a branch protection rule or a real pull request behaves the same way. That is what the checklist run on real runners is for.

    python3 scripts/ci_smoke/run_local.py            # all cases, exit 0 only when every step gave its expected result
    python3 scripts/ci_smoke/run_local.py c d        # some cases

Needs the package installed (`pip install -e .`) and git. Stdlib only.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
STORY = 1001
_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
            **{f"GIT_{k}_{f}": v for k in ("AUTHOR", "COMMITTER") for f, v in (("NAME", "Smoke"), ("EMAIL", "smoke@example.invalid"))}}
_TOKEN_IDENTITY = "aaaa1111-0000-4000-8000-00000000000a"  # the CI token's identity: not an approver, no bypass


def load_cases():
    return json.loads((HERE / "cases.json").read_text(encoding="utf-8"))


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env={**os.environ, **_GIT_ENV}).stdout.strip()


def _copy_tree(name, repo):
    # copyfile, not copy2: kept modification times make git believe a same-size file did not change
    shutil.copytree(FIX / "tree" / name, repo, dirs_exist_ok=True, copy_function=shutil.copyfile)


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def build(case, repo):
    """A repository whose `main` holds the case's target config and whose branch `feature` holds the story: spec, a red commit (the test fails), a docs-only
    commit, a green commit, then the ledger (the verification record is written by `ado strict verify`, the review and fidelity blocks from the fixtures)."""
    _git(repo, "init", "-q", "-b", "main")
    _copy_tree("base", repo)
    shutil.copy(FIX / case["target_config"], repo / "ado.config.json")
    if case.get("stack", True):
        shutil.copy(FIX / "ado.stack.json", repo / "ado.stack.json")
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", case["branch"])
    ref = f" AB#{STORY}" if case.get("story_refs", True) else ""  # a commit that names the story, as a strict target requires
    if case.get("head_config"):  # the PR carries its own config from its first commit
        shutil.copy(FIX / case["head_config"], repo / "ado.config.json")
    _copy_tree("spec", repo)
    _commit(repo, f"spec{ref}")
    _copy_tree("red", repo)
    red = _commit(repo, f"red{ref}")
    _copy_tree("wip", repo)
    wip = _commit(repo, f"wip{ref}")
    _copy_tree("green", repo)
    green = _commit(repo, f"green{ref}")
    claimed = {"green": green, "wip": wip}[case.get("claimed_green", "green")]  # a faked green names a commit at which the tests still fail
    ledger = repo / "dod" / f"DoD_{STORY}.md"
    ledger.parent.mkdir()
    ledger.write_text((FIX / "ledger.md").read_text(encoding="utf-8").replace("{RED}", red[:7]).replace("{GREEN}", claimed[:7]), encoding="utf-8")
    if case.get("record"):  # --base: the target's strict config judges, whatever the PR's own says
        code, _doc, _rules = run(["strict", "verify", str(STORY), "--base", "main"], repo)
        if code != 0:
            raise RuntimeError(f"building case {case['branch']}: the verification record could not be written (exit {code})")
    head = _git(repo, "rev-parse", "HEAD")
    blocks = (FIX / "ledger_blocks.md").read_text(encoding="utf-8").replace("{HEAD12}", head[:12]).replace("{HEAD}", head)
    ledger.write_text(ledger.read_text(encoding="utf-8").replace("## 5. DoD", blocks + "## 5. DoD"), encoding="utf-8")
    if case.get("decisions"):
        shutil.copy(FIX / case["decisions"], repo / "dod" / f"Decisions_{STORY}.md")
    _commit(repo, f"ledger{ref}")


class SmokeClient:
    """Stands in for the ADO client: the board tasks, the story, the permission and identity reads and the comments come from fixtures; any other
    request raises, so nothing can reach a network or go unnoticed."""

    def __init__(self, config):
        from ado.client import AdoError
        self.config, self._err, self.calls = config, AdoError, []
        self.comments = json.loads((FIX / "comments.json").read_text(encoding="utf-8"))

    def server_now(self):
        return None

    def request(self, method, path, body=None, content_type="application/json", raw=False):
        self.calls.append((method, path))
        if method == "POST" and "/_apis/wit/wiql" in path:
            return {"workItems": [{"id": 10}, {"id": 11}]}
        if method == "GET":
            if "/_apis/wit/workitems?ids=" in path:
                return {"value": [{"id": i, "fields": {"System.Title": f"[sk:T00{n}] x", "System.State": "To Do"}} for n, i in ((1, 10), (2, 11))]}
            if "/_apis/wit/workitems/" in path and "/comments" not in path and "/revisions" not in path:
                return {"id": STORY, "fields": {"System.State": "Active", "System.Title": "Members list"}, "relations": []}
            if path.startswith("/_apis/projects/"):
                return {"id": "99999999-9999-4999-8999-999999999999"}
            if path.startswith("/_apis/permissions/"):
                return {"count": 1, "value": [False]}  # the token identity cannot bypass work item rules
            if path.startswith("/_apis/connectionData"):
                return {"authenticatedUser": {"id": _TOKEN_IDENTITY, "descriptor": "aad.fictional"}}
            if "/revisions?" in path:
                return {"value": []}
            if "/comments?" in path and "/versions" not in path:
                return {"comments": self.comments}
        raise self._err("the smoke client does not answer this request")

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body, **kw):
        return self.request("POST", path, body, **kw)

    def patch(self, path, body, **kw):
        return self.request("PATCH", path, body, **kw)


def run(argv, repo):
    """(exit code, parsed stdout or None, finding rule ids) of `ado <argv>` run in `repo`, in this process, with the smoke client and no token."""
    from ado import __main__ as cli
    out, err, old_cwd, old_env = io.StringIO(), io.StringIO(), os.getcwd(), dict(os.environ)
    os.environ.update(_GIT_ENV)
    for key in ("ADO_CONFIG", "ADO_TOKEN"):
        os.environ.pop(key, None)
    os.environ["ADO_TOKEN"] = "smoke-not-a-token"
    real_client, cli.AdoClient = cli.AdoClient, SmokeClient
    os.chdir(repo)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as e:  # argparse
                code = e.code if isinstance(e.code, int) else 2
    finally:
        os.chdir(old_cwd)
        cli.AdoClient = real_client
        os.environ.clear()
        os.environ.update(old_env)
    run.last_stderr = err.getvalue()  # for a person debugging a differing case; never compared
    try:
        doc = json.loads(out.getvalue())
    except ValueError:
        doc = None
    findings = doc.get("findings") if isinstance(doc, dict) else None
    rules = [f.get("rule") for f in findings] if isinstance(findings, list) else []
    return code, doc, rules


def run_case(name, case):
    """[(argv, expected, actual)] for every step whose result differs from the expectation (empty: the case passed). A step's `out` is a subset of the
    JSON the command printed (the strict mode and its source, `ok`, `ready`): the proof that the strict rules were the ones evaluated."""
    tmp = Path(tempfile.mkdtemp(prefix=f"ci-smoke-{name}-"))
    try:
        repo = tmp / "repo"
        repo.mkdir()
        build(case, repo)
        _git(repo, "checkout", "-q", case["branch"])
        bad = []
        for step in case["steps"]:
            code, doc, rules = run(step["argv"], repo)
            want = {"exit": step["exit"], "rules": step.get("rules", []), "out": step.get("out", {})}
            shown = doc if isinstance(doc, dict) else {}
            got = {"exit": code, "rules": rules, "out": {k: shown.get(k, "<absent>") for k in want["out"]}}
            if got != want:
                bad.append((step["argv"], want, got))
        return bad
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    cases, wanted = load_cases(), (argv if argv is not None else sys.argv[1:])
    unknown = [w for w in wanted if w not in cases]
    if unknown:
        print(f"unknown case(s): {' '.join(unknown)}; known: {' '.join(cases)}", file=sys.stderr)
        return 2
    failed = 0
    for name in wanted or cases:
        bad = run_case(name, cases[name])
        failed += bool(bad)
        print(f"({name}) {cases[name]['title']}: {'ok' if not bad else 'DIFFERS'}")
        for step_argv, want, got in bad:
            print(f"    ado {' '.join(step_argv)}: expected {want}, got {got}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
