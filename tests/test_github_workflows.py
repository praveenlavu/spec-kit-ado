"""Static checks on .github/workflows (actionlint itself runs in CI)."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WF_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
REUSABLE = ["ado-link-on-merge", "ado-gate", "ado-branch-guard"]
SECRETFREE = ["ado-strict-verify"]  # V3-14b: reusable, but declares no secret at all
ALL = REUSABLE + SECRETFREE + ["ci", "commit-lint", "speckit-contract"]
UNTRUSTED = re.compile(
    r"\$\{\{[^}]*(github\.event\.pull_request\.(title|body|head\.ref)|github\.head_ref)[^}]*\}\}"
)


def load(name):
    return yaml.safe_load((WF_DIR / f"{name}.yml").read_text())


def triggers(wf):
    return wf.get("on", wf.get(True))  # PyYAML parses bare `on` as True


def steps(wf):
    for job in wf["jobs"].values():
        yield from job["steps"]


def gate_run():
    """The gate workflow's two scripts (run gate; post and enforce) as one text, in step order."""
    return "\n".join(s["run"] for s in steps(load("ado-gate")) if s.get("name") in GATE_STEP_NAMES)


GATE_STEP_NAMES = ("Run gate", "Post summary and enforce")


def test_gate_step_env_has_no_github_token_and_the_post_step_has_no_ado_token():
    by_name = {s["name"]: s for s in steps(load("ado-gate")) if s.get("name") in GATE_STEP_NAMES}
    assert set(by_name) == set(GATE_STEP_NAMES)
    assert "ado gate" in by_name["Run gate"]["run"] and "ADO_TOKEN" in by_name["Run gate"]["env"]
    assert "GH_TOKEN" not in by_name["Run gate"]["env"] and not re.search(r"(^|\s)gh (api|pr)\b", by_name["Run gate"]["run"])
    assert "GH_TOKEN" in by_name["Post summary and enforce"]["env"]
    assert "ADO_TOKEN" not in by_name["Post summary and enforce"]["env"] and 'ado gate "' not in by_name["Post summary and enforce"]["run"]


def test_expected_files_and_no_selftest():
    assert sorted(p.stem for p in WF_DIR.glob("*.yml")) == sorted(ALL)


@pytest.mark.parametrize("name", ALL)
def test_no_untrusted_expressions_in_run(name):
    for step in steps(load(name)):
        run = step.get("run", "")
        assert not UNTRUSTED.search(run), f"{name}: untrusted value in run: {run[:80]}"
        # stricter: any expression in run: is a smell (values must come via env:)
        assert "${{" not in run, f"{name}: expression inside run: {run[:80]}"


@pytest.mark.parametrize("name", REUSABLE)
def test_reusable_workflows_are_workflow_call(name):
    wf = load(name)
    t = triggers(wf)
    assert "workflow_call" in t
    tok = t["workflow_call"]["secrets"]["TOOLKIT_READ_TOKEN"]
    assert tok.get("required") is False
    assert t["workflow_call"]["inputs"]["toolkit-ref"]["default"] == "v0"
    text = (WF_DIR / f"{name}.yml").read_text()
    assert "praveenlavu/spec-kit-ado" in text
    assert "pip install --quiet ./.ado-toolkit" in text
    assert "secrets.TOOLKIT_READ_TOKEN || github.token" in text


def test_ado_gate_blocks_only_on_exit_1_and_comments_via_file():
    text = (WF_DIR / "ado-gate.yml").read_text()
    assert "ado gate" in text and "--changed" in text
    assert '--base "origin/${BASE_REF}"' in text  # env value, quoted
    assert "gh pr comment" in text and "--body-file" in text
    assert "mapfile" in text


def test_link_on_merge_runs_done_apply():
    text = (WF_DIR / "ado-link-on-merge.yml").read_text()
    assert "ado done" in text and "--apply" in text
    assert "ado comment" in text and "--state" in text


def test_ci_runs_scrub_and_pytest():
    wf = load("ci")
    t = triggers(wf)
    assert "pull_request" in t and t["push"]["branches"] == ["main"]
    text = (WF_DIR / "ci.yml").read_text()
    runs = "\n".join(s.get("run", "") for s in steps(wf))
    assert "pytest" in runs
    assert "scripts/scrub_check.py" in runs
    assert "secrets.SCRUB_DENYLIST" in text
    assert "--require-denylist" in runs and "--history" in runs
    assert "fetch-depth: 0" in text
    assert "actionlint" in text
    # strict scrub flags only on push to main
    for step in steps(wf):
        if "--require-denylist" in step.get("run", ""):
            assert "push" in step.get("if", "")


def test_commit_lint_triggers_on_pr():
    assert "pull_request" in triggers(load("commit-lint"))


def _commit_lint_run():
    return next(st["run"] for st in steps(load("commit-lint")) if "Check commit messages" in st.get("name", ""))


def _lint_repo(tmp_path, subject, main_moves=False):
    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, text=True,
                              env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}).stdout.strip()
    git("init", "-q", "-b", "main")
    (tmp_path / "a").write_text("1")
    git("add", "a")
    git("commit", "-q", "-m", "chore: base\n\nbody")
    base = git("rev-parse", "HEAD")
    git("switch", "-q", "-c", "pr")
    (tmp_path / "b").write_text("2")
    git("add", "b")
    git("commit", "-q", "-m", subject + "\n\nbody")
    if main_moves:  # main is fast-forwarded to the PR tip right after the PR event (what ship.sh did)
        git("update-ref", "refs/remotes/origin/main", git("rev-parse", "HEAD"))
    return base


def _lint(tmp_path, base):
    return subprocess.run(["bash", "-c", _commit_lint_run()], cwd=tmp_path, capture_output=True, text=True,
                          env={**os.environ, "BASE_SHA": base})


def test_commit_lint_lints_from_the_pr_base_commit_even_when_main_has_moved(tmp_path):
    """The old range origin/<base>..HEAD was empty once main was pushed to the PR tip, so the check passed without linting."""
    base = _lint_repo(tmp_path, "feat: " + "x" * 80, main_moves=True)
    r = _lint(tmp_path, base)
    assert r.returncode == 1 and "max 72" in r.stdout


def test_commit_lint_passes_a_good_commit_and_counts_it(tmp_path):
    base = _lint_repo(tmp_path, "chore: a short good subject")
    r = _lint(tmp_path, base)
    assert r.returncode == 0 and "All 1 commit messages pass." in r.stdout


def test_commit_lint_refuses_an_empty_range_and_a_bad_base(tmp_path):
    base = _lint_repo(tmp_path, "chore: a short good subject")
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert _lint(tmp_path, head).returncode == 1  # nothing between base and HEAD: do not pass vacuously
    assert _lint(tmp_path, "main").returncode == 1  # not a 40-hex id
    assert _lint(tmp_path, "0" * 40).returncode == 1  # not in the checkout


@pytest.mark.parametrize("name", ALL)
def test_every_workflow_has_least_privilege_permissions(name):
    perms = load(name).get("permissions")
    assert perms and perms.get("contents") == "read"
    extra = {k for k in perms if k != "contents"}
    assert extra == ({"pull-requests"} if name == "ado-gate" else set())


@pytest.mark.parametrize("name", REUSABLE + SECRETFREE)
def test_toolkit_checkout_does_not_persist_credentials(name):
    toolkit = [s for s in steps(load(name))
               if s.get("with", {}).get("repository") == "praveenlavu/spec-kit-ado"]
    assert toolkit and all(s["with"].get("persist-credentials") is False for s in toolkit)


def test_ado_gate_fails_only_on_blocking_and_uses_nul_names():
    run = gate_run()
    assert 'exit "$code"' not in run
    assert "blocking" in run and "json.load" in run
    assert "git diff -z --name-only" in run and "mapfile -d ''" in run


def test_link_on_merge_dry_runs_before_apply_and_is_resilient():
    run = next(s["run"] for s in steps(load("ado-link-on-merge")) if "ado done" in s.get("run", ""))
    assert "--plan-out" in run
    assert run.index("--plan-out") < run.index("--apply")
    assert "::warning::" in run


def test_ado_gate_comment_comes_from_cli_summary():
    run = gate_run()
    assert '--summary-out "$RUNNER_TEMP/gate.md"' in run and '> "$RUNNER_TEMP/gate.json"' in run
    assert "ADO_TOKEN" not in run and "${msg" not in run
    assert "--edit-last" not in run  # it can overwrite another workflow's comment
    assert "could not run (see job log)" in run
    blocking, strict = [ln for ln in run.splitlines() if "exit 1" in ln]  # P3-13d: the blocking line first (its verdict is not 'could not run'), then the strict-target line
    assert '"$MODE" = strict' in strict and '"$code" -ne 0' in strict
    assert '"$code" -eq 1' in blocking and '["blocking"] is True' in blocking


def test_branch_guard_fails_only_on_confirmed_not_found():
    text = (WF_DIR / "ado-branch-guard.yml").read_text()
    assert "HTTP 404" in text
    assert "could not be verified" in text
    steps_ = list(steps(load("ado-branch-guard")))
    assert not any("${{" in s.get("run", "") for s in steps_)
    assert next(s for s in steps_ if "--id-from" in s.get("run", ""))["env"]["BRANCH"] == "${{ github.head_ref }}"


def test_link_on_merge_closes_story_only_when_opted_in():
    wf = load("ado-link-on-merge")
    inp = triggers(wf)["workflow_call"]["inputs"]["close-story"]
    assert inp["type"] == "boolean" and inp["default"] is False
    step = next(s for s in steps(wf) if "ado done" in s.get("run", ""))
    assert step["env"]["CLOSE_STORY"] == "${{ inputs.close-story }}"
    run = step["run"]
    # `ado done` lives in close_out(), which the opt-in guard alone calls
    assert run.count("close_out") == 2 and 'case "$CLOSE_STORY" in [Tt]rue) close_out && closed=true ;; esac' in run


@pytest.mark.parametrize("name", REUSABLE + SECRETFREE)
def test_toolkit_checkout_removed_before_any_ado_call(name):
    # The toolkit's own tests must not be scanned as the consumer's (`ado gate --src .`).
    runs = "\n".join(s.get("run", "") for s in steps(load(name)))
    install = runs.index("pip install --quiet ./.ado-toolkit")
    removed = runs.index("rm -rf .ado-toolkit", install)
    first_ado = re.search(r"^\s*(if )?ado ", runs[install:], re.M)
    assert first_ado and removed < install + first_ado.start()


# ---- PR #1 review fixes F + G ----
CI_FILES = sorted(WF_DIR.glob("ado-*.yml")) + sorted((WF_DIR.parent.parent / "pipelines").glob("ado-*.yml"))
PREFIXED = 'changed=("${changed[@]/#/./}")'


@pytest.mark.parametrize("path", [WF_DIR / "ado-gate.yml", WF_DIR.parent.parent / "pipelines" / "ado-gate.yml"])
def test_changed_names_prefixed_so_a_dash_name_is_not_an_option(path):
    text = path.read_text()
    assert PREFIXED in text and text.index(PREFIXED) < text.index('--changed "${changed[@]}"')


def test_story_id_regexes_accept_any_length():
    assert len(CI_FILES) == 8
    for p in CI_FILES:
        text = p.read_text()
        assert "{3,6}" not in text and "3-6" not in text, p.name
        assert "grep -oE '[0-9]+'" not in text, p.name  # no digit-run guessing
        assert "grep -oiE 'AB#" not in text, p.name  # AB# is word-bounded and case-sensitive like the CLI


@pytest.mark.parametrize("path", [WF_DIR / "ado-branch-guard.yml", WF_DIR.parent.parent / "pipelines" / "ado-branch-guard.yml",
                                  WF_DIR.parent.parent / "pipelines" / "ado-gate.yml"])
def test_branch_ci_reads_the_story_with_the_cli_rule(path):
    text = path.read_text()
    assert 'ado branch --id-from="$branch"' in text or 'ado branch --id-from="$BRANCH"' in text
    assert "for c in" not in text and "for id in" not in text.replace("ado get", "")


def test_ado_gate_removes_stale_summary_before_running():
    run = gate_run()
    assert run.index('rm -f "$RUNNER_TEMP/gate.md"') < run.index('ado gate "$STORY_ID"')


WARN_GH = 'echo "::warning::spec-kit-ado gate could not run (see the PR comment and job log)"'


def test_ado_gate_warns_when_gate_could_not_run():
    run = gate_run()
    assert run.count(WARN_GH) == 1
    (line,) = [ln for ln in run.splitlines() if WARN_GH in ln]
    assert '"$code" -eq 0' in line and '"$code" -eq 1' in line and "blocking" in line
    assert "$" not in WARN_GH


def test_ado_gate_failed_diff_counts_as_could_not_run():
    run = gate_run()
    assert run.index("else code=2; fi") < run.index(WARN_GH)


def test_ado_gate_edits_only_its_own_marked_comment():
    run = gate_run()
    mark = "<!-- spec-kit-ado:gate -->"
    assert run.index(f"echo '{mark}'; cat \"$RUNNER_TEMP/gate.md\"; }} > \"$RUNNER_TEMP/comment.md\"") < run.index("gh api")
    assert 'select(.user.login == "github-actions[bot]"' in run and f'startswith("{mark}")' in run
    assert "-X PATCH" in run and '-F body=@"$RUNNER_TEMP/comment.md"' in run
    assert 'gh pr comment "$PR_NUM" --body-file "$RUNNER_TEMP/comment.md"' in run


ROOT = WF_DIR.parent.parent
INVALID_CFG = '{"organization": "contoso", "project": "Fabrikam", "gate": {"block": ["nope"]}}'
GUARDS = [(WF_DIR / "ado-branch-guard.yml", "BRANCH"), (ROOT / "pipelines" / "ado-branch-guard.yml", "SOURCE_BRANCH")]


def _guard_snippet(path):
    """The guard's shell (GitHub dialect, or Azure's with $AGENT_TEMPDIRECTORY mapped), minus the pip install line."""
    if path.parent.name == "pipelines":
        run = next(s["script"] for s in yaml.safe_load(path.read_text())["steps"] if "--id-from" in s.get("script", ""))
        return run.replace("${AGENT_TEMPDIRECTORY", "${RUNNER_TEMP").replace("$AGENT_TEMPDIRECTORY", "$RUNNER_TEMP")
    run = next(s["run"] for s in steps(load("ado-branch-guard")) if "--id-from" in s.get("run", ""))
    return "\n".join(l for l in run.splitlines() if "pip install" not in l) + "\n"


def _gate_id_snippet():
    script = next(s["script"] for s in yaml.safe_load((ROOT / "pipelines" / "ado-gate.yml").read_text())["steps"]
                  if "--id-from" in s.get("script", ""))
    return script[:script.index("# File names go")].replace("${AGENT_TEMPDIRECTORY", "${RUNNER_TEMP").replace("$AGENT_TEMPDIRECTORY", "$RUNNER_TEMP") + 'echo "ID=$id"\n'


ADO_CLI = "import sys;sys.path.pop(0);from ado.__main__ import main;sys.exit(main())"  # the console script has no cwd on sys.path


def _run_snippet(tmp_path, snippet, branch, *, get_code=0, config=True, have_ado=True, temp=None, ado_body=None, flags=()):
    """Run a CI shell snippet; `ado branch` is the real CLI, `ado get` a stub. Returns (exit code, output)."""
    import os, subprocess, sys
    base_env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    probe = subprocess.run([sys.executable, "-c", ADO_CLI, "--help"], env=base_env, cwd=tmp_path, capture_output=True)
    assert probe.returncode == 0, probe.stderr  # this interpreter's own ado (never a PATH one); fail, don't skip
    if have_ado:  # `ado branch ...` -> this interpreter's CLI; `ado get` -> canned answer
        stub = tmp_path / "ado"
        stub.write_text("#!/bin/bash\n" + ado_body if ado_body else f'#!/bin/bash\n[ "$1" = branch ] || [ "$1" = strict ] && exec "{sys.executable}" -c \'{ADO_CLI}\' "$@"\n'
                        f'c={get_code}; [ $c = 0 ] && exit 0; echo "HTTP $c" >&2; exit 1\n')
        stub.chmod(0o755)
    cfg = tmp_path / "ado.config.json"
    if config:
        cfg.write_text(config if isinstance(config, str) else '{"organization": "contoso", "project": "Fabrikam"}')
    script = tmp_path / "snippet.sh"
    script.write_text(snippet)
    env = {**base_env, "PATH": (f"{tmp_path}:" if have_ado else "") + "/usr/bin:/bin", "RUNNER_TEMP": str(temp or tmp_path),
           "ADO_CONFIG": str(cfg), "BRANCH": branch, "SOURCE_BRANCH": branch, "TARGET_BRANCH": "refs/heads/main"}
    if temp is False:  # the runner did not set its temp dir
        env.pop("RUNNER_TEMP")
    r = subprocess.run(["bash", *flags, str(script)], env=env, cwd=tmp_path, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


@pytest.mark.parametrize("path,var", GUARDS)
@pytest.mark.parametrize("branch,kw,expect,word", [
    ("feature/ab12-x", {}, 0, "verified"),
    ("refs/heads/feature/ab12-x", {}, 0, "verified"),
    ("fix AB#7", {}, 0, "verified"),
    ("001-x", {}, 1, "not tied"),
    ("feature/1001-x", {}, 1, "not tied"),
    ("--help", {}, 1, "not tied"),  # a leading dash is a name, not an option
    ("feature/ab99-x", {"get_code": 404}, 1, "does not exist"),
    ("feature/ab99-x", {"get_code": 500}, 0, "could not be verified"),
    ("001-x", {"config": False}, 0, "warning"),  # ado exit 2: no config
    ("feature/ab12-x", {"have_ado": False}, 0, "warning"),  # ado exit 127: never red
    ("feature/ab12-x", {"config": INVALID_CFG}, 0, "warning"),  # ado exit 3: ado-gate fails on it, not the guard
])
def test_guard_snippet_runs_real_id_from(tmp_path, path, var, branch, kw, expect, word):
    rc, out = _run_snippet(tmp_path, _guard_snippet(path), branch, **kw)
    assert rc == expect and word in out, out
    if kw.get("config") is False or kw.get("have_ado") is False or kw.get("config") == INVALID_CFG:
        assert "Could not read the story id" in out, out


@pytest.mark.parametrize("branch,kw,id_line", [
    ("feature/ab12-x", {}, "ID=12"), ("001-x", {}, None), ("001-x", {"config": False}, None)])
def test_azure_gate_id_step_skips_never_fails(tmp_path, branch, kw, id_line):
    rc, out = _run_snippet(tmp_path, _gate_id_snippet(), branch, **kw)
    if id_line is not None:
        assert rc == 0 and id_line in out and "skipped" not in out, out
    else:  # no id or no config: exit 0 (skipped) before the "ID=" echo
        assert rc == 0 and "skipped" in out and "ID=" not in out, out


# ---- F29: the gate step fails closed on exit 3 (invalid config), runs the real CLI ----
def _gate_step_run(path):
    if path.parent.name == "pipelines":
        run = next(s["script"] for s in yaml.safe_load(path.read_text())["steps"] if "ado gate" in s.get("script", ""))
        return run.replace("${AGENT_TEMPDIRECTORY", "${RUNNER_TEMP").replace("$AGENT_TEMPDIRECTORY", "$RUNNER_TEMP").replace("${TARGET_BRANCH#refs/heads/}", "main")
    run = gate_run()
    return run.replace('"origin/${BASE_REF}"', "origin/main").replace("origin/${BASE_REF}", "origin/main")


def _run_gate_step(tmp_path, path, config, *, block=(), temp=None, ado_body=None, extra_env=None, flags=()):
    import os, subprocess, sys
    git = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "init")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    if config is not None:
        (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", **config}))
    if block:  # a ledger with an untested AC, so a listed check blocks
        from ado.dod import SECTION2
        (tmp_path / "dod").mkdir()
        (tmp_path / "dod" / "DoD_1001.md").write_text(
            f"{SECTION2}\n## 3. AC\n| AC | x | tests | ev | status |\n|---|---|---|---|---|\n| AC-1 | x | Gone.testX | | red |\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("gh", "exit 0\n"),  # posting the comment succeeds
                       ("ado", ado_body or f'[ "$1 $2" = "pr comment" ] && exit 0\nexec "{sys.executable}" -c \'{ADO_CLI}\' "$@"\n')):
        (bindir / name).write_text("#!/bin/bash\n" + body)
        (bindir / name).chmod(0o755)
    # bash 3.2 (macOS) has no mapfile: feed the same array the real step builds from `git diff -z`
    run = _gate_step_run(path).replace("mapfile -d '' changed < changed.bin", "changed=(a.py)")
    run = run.replace("mapfile -d '' changed < \"$AGENT_TEMPDIRECTORY/changed.bin\"", "changed=(a.py)")
    run = run.replace('mapfile -d \'\' changed < "$RUNNER_TEMP/changed.bin"', "changed=(a.py)")
    script = tmp_path / "step.sh"
    script.write_text(run)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PATH": f"{bindir}:/usr/bin:/bin", "RUNNER_TEMP": str(temp or tmp_path),
           "ADO_CONFIG": str(tmp_path / "ado.config.json"), "STORY_ID": "1001", "BASE_REF": "main", "PR_NUM": "7",
           "GITHUB_REPOSITORY": "contoso/app", "PR_ID": "7", "SOURCE_BRANCH": "refs/heads/feature/ab1001-x",
           "TARGET_BRANCH": "refs/heads/main", "HEAD_SHA": "0123456789abcdef0123456789abcdef01234567"}
    env.pop("ADO_TOKEN", None)
    env["HOME"] = str(tmp_path)  # no real keychain/.azdo token
    env.update(extra_env or {})
    if temp is False:  # the runner did not set its temp dir
        env.pop("RUNNER_TEMP")
    r = subprocess.run(["bash", *flags, str(script)], env=env, cwd=tmp_path, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr, tmp_path


GATE_STEPS = [WF_DIR / "ado-gate.yml", ROOT / "pipelines" / "ado-gate.yml"]


@pytest.mark.parametrize("path", GATE_STEPS)
@pytest.mark.parametrize("config,block,rc,word", [
    ({"gate": {"block": ["nope"]}}, (), 3, "ado.config.json is invalid"),  # explicit misconfiguration: red
    (None, (), 0, "advisory"),                                             # missing config: advisory
    ({}, (), 0, None),                                                      # valid config, nothing to block
    ({"gate": {"block": ["ac_traceability"]}}, ("x",), 1, None),            # blocking finding: red
])
def test_gate_step_fails_closed_only_on_invalid_config_or_blocking(tmp_path, path, config, block, rc, word):
    got, out, d = _run_gate_step(tmp_path, path, config, block=block)
    if rc == 3 and path.parent.name == "pipelines":
        rc = 1  # Azure fails the story-id step (exit 1) before the gate runs; GitHub exits with the CLI's 3
    assert got == rc, out
    if word:
        md = (d / "gate.md").read_text() if (d / "gate.md").exists() else ""
        assert word in out + md, out
    if word == "ado.config.json is invalid":
        assert word in out  # the error annotation


def test_azure_gate_id_step_fails_the_build_on_an_invalid_config(tmp_path):
    rc, out = _run_snippet(tmp_path, _gate_id_snippet(), "feature/ab12-x", config=INVALID_CFG)
    assert rc == 1, out  # ado branch --id-from exits 3; the step turns it into a red build
    assert "##vso[task.logissue type=error]ADO gate FAILED: ado.config.json is invalid \u2014 error: " in out
    assert "unknown check (valid: " in out and "nope" not in out and "skipped" not in out and "ID=" not in out


def test_azure_gate_error_annotation_cannot_inject_a_vso_command(tmp_path):
    hostile = ('{"organization": "contoso", "project": "Fabrikam", "gate": {"block": '
               '["##vso[task.complete result=Succeeded]"]}}')
    rc, out = _run_snippet(tmp_path, _gate_id_snippet(), "feature/ab12-x", config=hostile)
    assert rc == 1, out
    assert out.count("##vso[") == 1  # only our own annotation; the hostile text is defanged
    assert "task.complete" not in out  # the config text is not echoed at all


# ---- F30: every annotation that interpolates untrusted text goes through the `ann` sanitiser ----
ANN_FAMILIES = {  # the shell function is byte-identical within a family (templates are copied one by one, so no shared file)
    "azure": ("##vso[task.logissue type=warning]", [ROOT / "pipelines" / "ado-gate.yml", ROOT / "pipelines" / "ado-branch-guard.yml",
                                                    ROOT / "pipelines" / "ado-link-on-merge.yml", ROOT / "pipelines" / "ado-strict-verify.yml"]),
    "github": ("::warning::", [WF_DIR / "ado-branch-guard.yml", WF_DIR / "commit-lint.yml", WF_DIR / "ado-gate.yml",
                               WF_DIR / "ado-link-on-merge.yml", WF_DIR / "ado-strict-verify.yml"]),
}
ANN_FILES = [p for _, ps in ANN_FAMILIES.values() for p in ps]
HOSTILE = {
    "vso-complete": "##vso[task.complete result=Succeeded]",
    "triple-hash": "###vso[x]",
    "crlf": "ok\r\n##vso[task.complete result=Failed]\n::set-output name=x::y\r",
    "gh-command": "::set-output name=x::y",
    "controls": "a\x1b[31m\x00\x01b\x7f\x85c ##vso[x] d",
    "long": "x" * 5000,
    "long-hash": "#" * 5000,
    "multibyte-boundary": "a" * 299 + "é" * 5,
    "multibyte-boundary-even": "a" * 298 + "é" * 5,
    "emoji-boundary": "a" * 299 + "\U0001F600" * 5,
    "invalid-utf8": "bad \udcff byte",
}


def _ann_line(path):
    """The template's `ann` definition. A step cannot see another step's shell functions, so a template whose several steps sanitise
    (V3-14a: the strict-mode step next to the gate step) defines it once per step: every copy must be byte-identical."""
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip().startswith("ann() {")]
    assert lines and len(set(lines)) == 1, path
    return lines[0]


@pytest.mark.parametrize("family", ANN_FAMILIES)
def test_ann_function_is_identical_in_every_template_of_a_family(family):
    copies = {_ann_line(p) for p in ANN_FAMILIES[family][1]}
    assert len(copies) == 1, copies
    assert ('replace("#","# ")' in copies.pop()) == (family == "azure")  # '#' is only special to Azure


@pytest.mark.parametrize("name", HOSTILE)
@pytest.mark.parametrize("family", ANN_FAMILIES)
def test_ann_neutralises_hostile_text_in_a_real_bash(tmp_path, family, name):
    import os, subprocess
    prefix, paths = ANN_FAMILIES[family]
    script = f'{_ann_line(paths[0])}\nprintf \'%s\\n\' "{prefix}$(ann)"\n'
    raw = HOSTILE[name]
    r = subprocess.run(["bash", "-c", script], env={**os.environ, "LC_ALL": "C"}, capture_output=True, cwd=tmp_path,
                       input=raw.encode("utf-8", "surrogateescape"))  # raw bytes on stdin, NUL and invalid UTF-8 included
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode("utf-8")  # strict: a split multibyte character fails here
    assert out.endswith("\n") and out.count("\n") == 1, repr(out)  # one line
    line = out[:-1]
    assert line.startswith(prefix) and not re.search(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", line)
    text = line[len(prefix):]
    assert len(text) <= 300
    if family == "azure":
        assert "##" not in text and line.count("##vso[") == 1  # only the template's own command
    else:
        assert line.splitlines() == [line]  # flattened: a hostile `::cmd::` can never start a line
    if name == "multibyte-boundary":
        assert text == "a" * 299 + "é"  # cut on a character, not a byte
    if name == "invalid-utf8":
        assert text.startswith("bad ")


def test_untrusted_text_in_annotations_always_goes_through_ann():
    risky = re.compile(r"\.err\b|\$branch|\$BRANCH|\$subject|\$\{?(SOURCE_BRANCH|PR_TITLE)")
    annotation = re.compile(r"echo \"(##vso\[task\.logissue|::(error|warning|notice)::|ado gate: |ado strict (mode|check|verify): )")  # log lines that carry CLI stderr count too
    uses_ann = re.compile(r"\| ann\)|ann < ")
    for p in ANN_FILES:
        lines = [l for l in p.read_text().splitlines() if annotation.search(l)]
        untrusted = [l for l in lines if risky.search(l)]
        for l in untrusted:
            assert uses_ann.search(l), f"{p.name}: {l.strip()}"
        # the `risky` list is complete: an annotation that calls ann but matches no untrusted marker means the list is stale
        assert len(untrusted) == len([l for l in lines if uses_ann.search(l)]), p.name
        assert untrusted, p.name  # every file in the list has at least one such annotation


# ---- F31: the link-on-merge ledger-path echo (dod_path comes from the PR's config) goes through `ann` too ----
LINK_FILES = [WF_DIR / "ado-link-on-merge.yml", ROOT / "pipelines" / "ado-link-on-merge.yml"]


def _link_snippet(path):
    if path.parent.name == "pipelines":
        run = next(s["script"] for s in yaml.safe_load(path.read_text())["steps"] if "ado done" in s.get("script", ""))
        run = run.replace("${AGENT_TEMPDIRECTORY", "${RUNNER_TEMP").replace("$AGENT_TEMPDIRECTORY", "$RUNNER_TEMP")
        return re.sub(r"(?m)^ids=.*$", "ids=7", run, count=1).replace("${AGENT_TEMPDIRECTORY}", "${RUNNER_TEMP}")
    return next(s["run"] for s in steps(load("ado-link-on-merge")) if "ado done" in s.get("run", ""))


@pytest.mark.parametrize("path", LINK_FILES)
def test_link_on_merge_ann_matches_its_family_and_wraps_the_ledger_echo(path):
    family = "azure" if path.parent.name == "pipelines" else "github"
    assert _ann_line(path) == _ann_line(ANN_FAMILIES[family][1][0])
    assert 'no ledger at $(printf \'%s\' "$ledger" | ann)' in path.read_text()


@pytest.mark.parametrize("path", LINK_FILES)
@pytest.mark.parametrize("dod_path", [
    "x\n::error::pwned",
    "x\r\n##vso[task.complete result=Failed]",
    "x ::error::pwned ##vso[x]",
    "x\x1b\x00::error::pwned",
])
def test_link_on_merge_ledger_echo_with_a_hostile_dod_path_stays_one_line(tmp_path, path, dod_path):
    cfg = json.dumps({"organization": "contoso", "project": "Fabrikam", "dod_path": dod_path})
    snippet = ('export IDS=7 CLOSE_STORY=true STATE= PR_NUM=1 PR_URL=u BRANCH=main SOURCE_VERSION=abcdef123456\n'
               + _link_snippet(path))
    rc, out = _run_snippet(tmp_path, snippet, "main", config=cfg)
    assert rc == 0, out
    assert "7: no ledger at " in out and "skipped." in out, out
    for line in out.splitlines():
        assert not line.startswith(("::error::", "##vso[")), repr(line)
    assert not re.search(r"[\x00-\x1b\x7f-\x9f  ]", out.replace("\n", ""))


# ---- CI1: CI helpers run isolated (python3 -I) and write only to the runner temp directory ----
CI_TEMPLATES = CI_FILES + [WF_DIR / "commit-lint.yml"]


def _code(path):
    """A template's shell, comments stripped."""
    doc = yaml.safe_load(path.read_text())
    runs = [s.get("run") or s.get("script") or "" for s in (doc["steps"] if path.parent.name == "pipelines" else steps(doc))]
    return re.sub(r"(?m)(^|\s)#(\s.*|$)", "", "\n".join(runs))


@pytest.mark.parametrize("path", CI_TEMPLATES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_ci_python_runs_isolated_and_nothing_is_written_to_the_checkout(path):
    code = _code(path)
    assert not re.findall(r"\bpython3?\b(?! -I -c )", code)  # isolated mode: no cwd on sys.path, no PYTHON* env
    assert not _bad_writes(code)  # the only write targets are the runner temp dir, $GITHUB_OUTPUT and /dev/null


WRITE_TARGET = re.compile(r"""(?<![\w$])(?:
    (?<=[\s\d])(?:&>>?|>>?)(?![&>])\s*       # > >> &>   (2>&1 / >&2 are fd dups, not writes)
  | \btee\b(?:\s+-\w+)*                      # | tee [-a] FILE
  | (?<=\s)-o\s+                              # -o FILE
  | --(?:summary-out|body-file|plan-out)[\s=]+
  | body=@
)\s*(\S+)""", re.X)
ALLOWED_TARGET = re.compile(r"""["']?(\$\{?(RUNNER_TEMP|AGENT_TEMPDIRECTORY)\}?/|\$GITHUB_OUTPUT\b|\$GITHUB_STEP_SUMMARY\b|/dev/null\b)""")


def _bad_writes(code):
    return [m.group(1) for m in WRITE_TARGET.finditer(code) if not ALLOWED_TARGET.match(m.group(1))]


@pytest.mark.parametrize("snippet", [
    "ado gate 1 > gate.json", "x | tee comment.md", "x | tee -a comment.md", "grep -q y -o x", "x &> out.txt", "x >> log",
    'x > "$PWD/x"', "x > $GITHUB_WORKSPACE/x", 'x > "${GITHUB_WORKSPACE}/x"', 'x --summary-out "$HOME/gate.md"',
    "gh pr comment --body-file comment.md", "gh api -F body=@comment.md", "ado pr comment 1 --plan-out ./p.md",
    'x > "$RUNNER_TEMP_x/y"', "x > $GITHUB_OUTPUT_x",
])
def test_write_checker_flags_a_write_into_the_checkout(snippet):
    assert _bad_writes(snippet), snippet


@pytest.mark.parametrize("snippet", [
    'x > "$RUNNER_TEMP/a" 2> "${RUNNER_TEMP}/b"', "x 2>/dev/null >/dev/null", "x 2>&1; y >&2", 'echo a >> "$GITHUB_OUTPUT"',
    'x | tee -a "${AGENT_TEMPDIRECTORY}/x"', 'ado gate --summary-out "$AGENT_TEMPDIRECTORY/g.md"',
    'gh api -F body=@"$RUNNER_TEMP/c.md"', 'gh pr comment --body-file "$RUNNER_TEMP/c.md"', 'x >> "$GITHUB_STEP_SUMMARY"',
    "grep -oE 'a' f; x <<'EOF'",
])
def test_write_checker_allows_only_the_temp_dir_outputs_and_dev_null(snippet):
    assert not _bad_writes(snippet), snippet


MARKERS = ("re", "json", "os")


def _plant(tmp_path):
    """Planted stdlib look-alikes in the checkout: importing one drops a marker file."""
    (tmp_path / "markers").mkdir()
    for m in MARKERS:
        (tmp_path / f"{m}.py").write_text(f'open(r"{tmp_path}/markers/{m}", "w").close()\n')
    return lambda: sorted(p.name for p in (tmp_path / "markers").iterdir())


def _link_with_ledger(d):
    (d / "dod").mkdir(exist_ok=True)
    (d / "dod" / "DoD_7.md").write_text("x")  # ledger present: the `ready` reader runs too
    return 'export IDS=7 CLOSE_STORY=true STATE= PR_NUM=1 PR_URL=u BRANCH=main SOURCE_VERSION=abcdef123456\n'


BLOCKING = {"gate": {"block": ["ac_traceability"]}}
MARKER_SCENARIOS = {  # every shell snippet with a python3 call, on a path that reaches it
    "gate-github": lambda d: _run_gate_step(d, GATE_STEPS[0], BLOCKING, block=("x",))[:2],  # exit 1 + blocking: both readers run
    "gate-azure": lambda d: _run_gate_step(d, GATE_STEPS[1], BLOCKING, block=("x",))[:2],
    "gate-azure-id": lambda d: _run_snippet(d, _gate_id_snippet(), "feature/ab12-x", config=INVALID_CFG),
    "guard-github": lambda d: _run_snippet(d, _guard_snippet(GUARDS[0][0]), "feature/ab12-x", config=INVALID_CFG),
    "guard-azure": lambda d: _run_snippet(d, _guard_snippet(GUARDS[1][0]), "feature/ab12-x", config=INVALID_CFG),
    "guard-github-untied": lambda d: _run_snippet(d, _guard_snippet(GUARDS[0][0]), "not-tied"),
    "guard-azure-untied": lambda d: _run_snippet(d, _guard_snippet(GUARDS[1][0]), "not-tied"),
    "link-github": lambda d: _run_snippet(d, _link_with_ledger(d) + _link_snippet(LINK_FILES[0]), "main"),
    "link-azure": lambda d: _run_snippet(d, _link_with_ledger(d) + _link_snippet(LINK_FILES[1]), "main"),
    "mode-github": lambda d: _gh_run(d, _gh("ado-gate", "mode"), MODE_ENV, ado='echo \'{"mode": "strict"}\'\n'),
    "strict-github": lambda d: _gh_run(d, _gh("ado-gate", "Strict check"), STRICT_ENV, ado=_findings_ado(d)),
    "enforce-github": lambda d: _gh_run(d, _gh("ado-gate", "Enforce strict check"), {}),
    "verify-github": lambda d: _verify_job(d, _v_ado(d, verify_rc=2)),
    "verify-azure": lambda d: _run_az_verify(d, _v_ado(d, verify_rc=2)),
    "gate-azure-strict": lambda d: _run_az(d),
    "link-github-refused": lambda d: _link_run(d, LINK_FILES[0], _refused_ado(d)),
    "link-azure-refused": lambda d: _link_run(d, LINK_FILES[1], _refused_ado(d)),
}


@pytest.mark.parametrize("name", MARKER_SCENARIOS)
def test_ci_python_ignores_planted_modules_in_the_checkout(tmp_path, name):
    seen = _plant(tmp_path)
    rc, out = MARKER_SCENARIOS[name](tmp_path)
    assert seen() == [], out  # a PR-committed re.py/json.py/os.py was imported
    assert "Traceback" not in out, out  # the python3 call itself ran fine


def test_commit_lint_ann_ignores_planted_modules_in_the_checkout(tmp_path):
    import os, subprocess
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a], cwd=tmp_path,
                                    check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "init")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    git("commit", "-q", "--allow-empty", "-m", "not conventional")
    seen = _plant(tmp_path)
    run = next(s["run"] for s in steps(load("commit-lint")) if "ann()" in s.get("run", ""))
    r = subprocess.run(["bash", "-c", run], env={**os.environ, "BASE_SHA": base}, cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 1 and "not conventional" in r.stdout, r.stdout + r.stderr  # the ann path ran
    assert seen() == []


@pytest.mark.parametrize("path", GATE_STEPS, ids=["github", "azure"])
def test_gate_step_never_writes_through_a_symlink_in_the_checkout(tmp_path, path):
    victim = tmp_path / "outside.txt"
    victim.write_text("ORIGINAL")
    work, temp = tmp_path / "pr", tmp_path / "runner-temp"
    work.mkdir()
    temp.mkdir()
    for name in ("changed.bin", "gate.json", "comment.md", "gate.md"):
        (work / name).symlink_to(victim)  # PR-committed symlinks
    rc, out, _ = _run_gate_step(work, path, {"gate": {"block": ["ac_traceability"]}}, block=("x",), temp=temp)
    assert rc == 1, out  # unchanged verdict: blocking finding
    assert victim.read_text() == "ORIGINAL", out
    assert (temp / "gate.json").exists() and (temp / "gate.md").exists()  # the scratch files live in the temp dir


UNSET_SCENARIOS = {
    "gate-github": lambda d: _run_gate_step(d, GATE_STEPS[0], BLOCKING, block=("x",), temp=False)[:2],
    "gate-azure": lambda d: _run_gate_step(d, GATE_STEPS[1], BLOCKING, block=("x",), temp=False)[:2],
    "guard-github": lambda d: _run_snippet(d, _guard_snippet(GUARDS[0][0]), "feature/ab12-x", temp=False),
    "guard-azure": lambda d: _run_snippet(d, _guard_snippet(GUARDS[1][0]), "feature/ab12-x", temp=False),
    "link-github": lambda d: _run_snippet(d, _link_with_ledger(d) + _link_snippet(LINK_FILES[0]), "main", temp=False),
    "link-azure": lambda d: _run_snippet(d, _link_with_ledger(d) + _link_snippet(LINK_FILES[1]), "main", temp=False),
    "mode-github": lambda d: _gh_run(d, _gh("ado-gate", "mode"), MODE_ENV, no_temp=True),
    "strict-github": lambda d: _gh_run(d, _gh("ado-gate", "Strict check"), STRICT_ENV, no_temp=True),
    "enforce-github": lambda d: _gh_run(d, _gh("ado-gate", "Enforce strict check"), {}, no_temp=True),
    "verify-mode-github": lambda d: _gh_run(d, _gh(VERIFY_WF, "mode"), VERIFY_MODE_ENV, no_temp=True),
    "verify-step-github": lambda d: _gh_run(d, _gh(VERIFY_WF, "verify"), VERIFY_STEP_ENV, no_temp=True),
    "verify-azure": lambda d: _run_az_verify(d, _v_ado(d), temp=False),
}


@pytest.mark.parametrize("name", UNSET_SCENARIOS)
def test_a_missing_temp_dir_variable_fails_the_step_loudly_and_writes_nothing(tmp_path, name):
    rc, out = UNSET_SCENARIOS[name](tmp_path)
    assert rc != 0 and "is not set - refusing to write scratch files" in out, (rc, out)
    scratch = ("changed.bin", "gate.json", "gate.md", "comment.md", "id.err", "get.err", "done_7.json", "plan_7.md")
    assert not [n for n in scratch if (tmp_path / n).exists() or Path("/", n).exists()]


# ---- CI2: `ado` stderr (config / ADO text) never reaches the log raw from the gate and link-on-merge templates ----
HOSTILE_ERR = "boom\n##vso[task.complete result=Succeeded]\n::error::pwned\n::set-output name=a::b\r\n###vso[x]"
STDERR_ADO = f"printf '%s\\n' $'{HOSTILE_ERR}' >&2\n"  # prints the hostile text on stderr
STDOUT_ADO = "echo '##vso[task.setvariable variable=SECRET]stdout-injected'\n"  # ADO-sourced text on stdout (thread / work item JSON)


def _assert_no_injected_command(out, path):
    for line in out.splitlines():
        assert not line.startswith(("::error::pwned", "::set-output", "##vso[task.complete", "###vso")), repr(line)
        if path.parent.name == "pipelines" and "##vso[" in line:  # Azure matches anywhere in a line: only the template's own fixed annotation
            assert line.startswith("##vso[task.logissue type=") and line.count("##vso[") == 1, repr(line)
        if line.startswith("::"):
            assert re.match(r"::(warning|notice|error)::", line), repr(line)
    assert "stdout-injected" not in out  # ado stdout never reaches the log
    assert "boom" in out  # the text itself is still visible, just flattened onto the annotation's own line


@pytest.mark.parametrize("path", GATE_STEPS, ids=["github", "azure"])
def test_gate_step_sanitises_ado_stderr(tmp_path, path):
    body = ('case "$1" in branch) echo 1001; exit 0;; strict) exit 2;; esac\n' + STDOUT_ADO + STDERR_ADO + "exit 1\n")  # gate and pr comment both fail loudly
    rc, out, _ = _run_gate_step(tmp_path, path, {}, ado_body=body)
    assert rc == 0, out  # advisory: a CLI crash never turns the build red
    _assert_no_injected_command(out, path)
    assert "ado gate: boom" in out
    if path.parent.name == "pipelines":
        assert "Could not post the PR comment" in out


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
@pytest.mark.parametrize("plan_ready", [True, False])
def test_link_on_merge_sanitises_ado_stderr(tmp_path, path, plan_ready):
    plan = "echo '{\"ready\": true}'; " if plan_ready else ""
    body = f'case "$*" in *--plan-out*) {plan}{STDERR_ADO}exit 1;; esac\n' + STDOUT_ADO + STDERR_ADO + "exit 1\n"
    snippet = ("export IDS=7 CLOSE_STORY=true STATE=Resolved PR_NUM=1 PR_URL=u BRANCH=main SOURCE_VERSION=abcdef123456\n"
               + _link_snippet(path))
    _link_with_ledger(tmp_path)
    rc, out = _run_snippet(tmp_path, snippet, "main", ado_body=body)
    assert rc == 0, out
    _assert_no_injected_command(out, path)
    for what in ("could not comment", "could not set state", "could not evaluate" if not plan_ready else "ado done --apply failed"):
        assert what in out, (what, out)


# ---- MERGE7: link-on-merge closes the story BEFORE any transition and logs what `ado done` really did ----
def _merge7_ado(plan, apply_json, apply_rc=0):
    """fake `ado`: logs every call, answers the dry run and the apply of story 7."""
    return ('echo "$*" >> calls.log\n'
            f'case "$*" in\n *--plan-out*) echo \'{plan}\'; exit 0;;\n'
            f' "done 7 --apply"*) echo \'{apply_json}\'; exit {apply_rc};;\nesac\nexit 0\n')


READY = '{"ready": true}'
READY_DONE = '{"ready": true, "already_done": true}'
CLOSED = '{"attached": [{"path": "p"}], "failed": [], "state": "Done"}'
CLOSED_ATTACHED_BEFORE = '{"attached": [], "failed": [], "state": "Done"}'
DONE_BEFORE_ATTACHED = '{"attached": [], "failed": [], "state": "Done", "already_done": true, "skipped": "already done"}'
DONE_BEFORE_ATTACH_NOW = '{"attached": [{"path": "p"}], "failed": [], "state": "Done", "already_done": true}'
MERGE7_CASES = [  # id: (plan, apply json, apply rc, state, ledger?, log must say, log must not say, update runs?)
    ("closed", READY, CLOSED, 0, "Done", True, "DoD complete - ledger attached, story closed.", "already done", False),
    ("closed-attached-before", READY, CLOSED_ATTACHED_BEFORE, 0, "Resolved", True, "ledger was already attached, story closed", "ledger attached", False),
    ("pre-closed", READY, DONE_BEFORE_ATTACHED, 0, "Resolved", True, "already done - ledger already attached", "story closed", False),
    ("pre-closed-attach-now", READY, DONE_BEFORE_ATTACH_NOW, 0, "Done", True, "already done - ledger attached now", "story closed", False),
    ("apply-fails", READY, '{"failed": [{"path": "p"}], "state": null}', 1, "Resolved", True, "ado done --apply failed", "story closed", True),
    ("unreadable-result", READY, 'not json', 0, "Resolved", True, "unreadable result", "story closed", True),
    ("not-ready", '{"ready": false}', "", 0, "Resolved", True, "DoD not ready", "story closed", True),
    ("no-ledger", READY, "", 0, "Resolved", False, "no ledger at", "story closed", True),
    ("no-transition-asked", READY, CLOSED, 0, "", True, "story closed", "already done", False),
    # FIN3: a story already in done_state is never transitioned, whatever happened to its ledger
    ("done-attach-fails", READY_DONE, '{"failed": [{"path": "p"}], "state": "Done", "already_done": true}', 1, "Resolved", True,
     "already done, state left unchanged", "story closed", False),
    # the dry-run GET failed (no already_done), the apply's own read says done: still no transition
    ("done-only-in-apply", READY, '{"failed": [{"path": "p"}], "state": "Done", "already_done": true}', 1, "Resolved", True,
     "already done, state left unchanged", "story closed", False),
    ("done-unreadable-result", READY_DONE, 'not json', 0, "Resolved", True, "state left unchanged", "story closed", False),
    ("done-not-ready", '{"ready": false, "already_done": true}', "", 0, "Resolved", True, "already done, state left unchanged", "story closed", False),
    ("open-not-ready-flag-false", '{"ready": false, "already_done": false}', "", 0, "Resolved", True, "DoD not ready", "state left unchanged", True),
]


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
@pytest.mark.parametrize("case", MERGE7_CASES, ids=[c[0] for c in MERGE7_CASES])
def test_link_on_merge_closes_before_transition_and_logs_the_real_outcome(tmp_path, path, case):
    _, plan, apply_json, rc_apply, state, ledger, says, not_says, update_runs = case
    if ledger:
        _link_with_ledger(tmp_path)
    snippet = (f"export IDS=7 CLOSE_STORY=true STATE={state} PR_NUM=1 PR_URL=u BRANCH=main SOURCE_VERSION=abcdef123456\n"
               + _link_snippet(path))
    rc, out = _run_snippet(tmp_path, snippet, "main", ado_body=_merge7_ado(plan, apply_json, rc_apply))
    assert rc == 0, out
    assert says in out and not_says not in out, out
    calls = (tmp_path / "calls.log").read_text().splitlines()
    updates = [i for i, c in enumerate(calls) if c.startswith("update ")]
    assert bool(updates) == update_runs, calls
    if update_runs and ledger:
        assert max(i for i, c in enumerate(calls) if c.startswith("done 7")) < updates[0], calls  # close first, then transition
    if not state:
        assert not updates


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_never_transitions_to_the_done_state_before_done_ran(tmp_path, path):
    # transition-state == done_state: the old order moved the story first, so `ado done` saw it closed and attached nothing
    _link_with_ledger(tmp_path)
    snippet = ("export IDS=7 CLOSE_STORY=true STATE=Done PR_NUM=1 PR_URL=u BRANCH=main SOURCE_VERSION=abcdef123456\n"
               + _link_snippet(path))
    rc, out = _run_snippet(tmp_path, snippet, "main", ado_body=_merge7_ado(READY, CLOSED))
    assert rc == 0, out
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert any(c.startswith("done 7 --apply") for c in calls) and not any(c.startswith("update ") for c in calls), calls


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_without_close_story_still_transitions(tmp_path, path):
    _link_with_ledger(tmp_path)  # a ledger exists, so only the opt-in guard keeps `ado done` from running
    snippet = ("export IDS=7 CLOSE_STORY=false STATE=Resolved PR_NUM=1 PR_URL=u BRANCH=main SOURCE_VERSION=abcdef123456\n"
               + _link_snippet(path))
    rc, out = _run_snippet(tmp_path, snippet, "main", ado_body=_merge7_ado(READY, CLOSED))
    assert rc == 0, out
    assert (tmp_path / "calls.log").read_text().splitlines() == ["comment 7 " + (
        "Merged via PR #1: u" if path.parent.name != "pipelines" else "Merged to main in commit abcdef12."),
        "update 7 --state Resolved"]


# ======================================================================================================================
# V3-14a: the shipped CI templates enforce strict (mode from the target branch, no skipping, strict check, strict done)
# ======================================================================================================================
# The shells the templates really run under: a GitHub `run:` is `bash -e {0}` (with `shell: bash`: `-eo pipefail`); an Azure `script:` is
# `bash --noprofile --norc` with NO -e. Every new shim test below runs under all three (the `shell_flags` fixture sets SHELL_FLAGS).
SHELL_CASES = {"bash-e": ["-e"], "bash-eo-pipefail": ["-eo", "pipefail"], "bash-plain": ["--noprofile", "--norc"]}
SHELL_FLAGS = ["-e"]


@pytest.fixture(params=list(SHELL_CASES))
def shell_flags(request, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "SHELL_FLAGS", SHELL_CASES[request.param])
    return SHELL_CASES[request.param]

SHA = "0123456789abcdef0123456789abcdef01234567"
STEP_MODE, STEP_STORY, STEP_STRICT, STEP_POST, STEP_ENFORCE = "mode", "story", "Strict check", "Post summary and enforce", "Enforce strict check"
AZ_GATE = ROOT / "pipelines" / "ado-gate.yml"
AZ_LINK = ROOT / "pipelines" / "ado-link-on-merge.yml"


def _gh(wf, key):
    """A step of a GitHub workflow by id or name."""
    return next(s for s in steps(load(wf)) if key in (s.get("id"), s.get("name")))


def _blocks(path):
    doc = yaml.safe_load(path.read_text())
    return [s.get("run") or s.get("script") for s in (doc["steps"] if path.parent.name == "pipelines" else steps(doc)) if s.get("run") or s.get("script")]


@pytest.mark.parametrize("path", CI_TEMPLATES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_shell_block_parses_with_bash_n(path):
    for text in _blocks(path):
        r = subprocess.run(["bash", "-n"], input=text, capture_output=True, text=True)
        assert r.returncode == 0, (path.name, r.stderr, text[:80])


# ---- static pins ----
def test_github_gate_strict_steps_are_ordered_and_hold_no_token_they_do_not_need():
    st = list(steps(load("ado-gate")))
    ids = [s.get("id") or s.get("name") for s in st]
    order = [STEP_MODE, STEP_STORY, "Run gate", STEP_STRICT, STEP_POST, STEP_ENFORCE]
    assert [i for i in ids if i in order] == order  # mode FIRST, then the story id, the gate, the strict check, the comment, the verdict
    mode, strict = _gh("ado-gate", STEP_MODE), _gh("ado-gate", STEP_STRICT)
    assert re.search(r'^\s*ado strict mode --base "origin/\$\{BASE_REF\}" > "\$RUNNER_TEMP/mode\.json"', mode["run"], re.M)
    assert mode["run"].index("ado strict mode") < mode["run"].index("::error::")
    assert set(mode["env"]) == {"ADO_CONFIG", "BASE_REF"}  # no token of any kind: the answer is local
    assert set(strict["env"]) == {"ADO_CONFIG", "STORY_ID", "BASE_REF", "HEAD_SHA"}  # the check is local too: no ADO_TOKEN, no GH_TOKEN
    assert strict["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert 'ado strict check "$STORY_ID" --phase pr --head "$HEAD_SHA" --base "origin/${BASE_REF}"' in strict["run"]
    assert strict["if"] == "steps.story.outputs.id != '' && steps.mode.outputs.mode == 'strict'"
    assert "ADO_TOKEN" not in strict["run"] and "GH_TOKEN" not in strict["run"] and not re.search(r"(^|\s)gh (api|pr)\b", strict["run"])
    assert _gh("ado-gate", STEP_ENFORCE)["if"] == strict["if"] and "env" not in _gh("ado-gate", STEP_ENFORCE)


def test_github_gate_advisory_path_steps_keep_their_shape():
    gate = _gh("ado-gate", "Run gate")  # the v0.2 step, untouched: same condition, same four env values
    assert gate["if"] == "steps.story.outputs.id != ''" and set(gate["env"]) == {"ADO_TOKEN", "ADO_CONFIG", "STORY_ID", "BASE_REF"}
    assert _gh("ado-gate", STEP_POST)["if"] == "steps.story.outputs.id != ''"
    assert set(_gh("ado-gate", STEP_POST)["env"]) == {"GH_TOKEN", "PR_NUM", "MODE"} and "ADO_TOKEN" not in _gh("ado-gate", STEP_POST)["env"]
    story = _gh("ado-gate", STEP_STORY)
    assert story["env"]["MODE"] == "${{ steps.mode.outputs.mode }}"  # only ever strict or advisory (validated by the mode step)
    assert triggers(load("ado-gate"))["workflow_call"]["inputs"]["config-path"]["default"] == "ado.config.json"


def test_github_link_on_merge_checks_out_the_merged_commit_in_full():
    first, toolkit = [s for s in steps(load("ado-link-on-merge")) if s.get("uses", "").startswith("actions/checkout")]
    assert first["with"] == {"ref": "${{ github.event.pull_request.merge_commit_sha }}", "fetch-depth": 0, "persist-credentials": False}
    assert first["if"] == "steps.ab.outputs.ids != ''" and toolkit["with"]["ref"] == "${{ inputs.toolkit-ref }}" and toolkit["with"]["repository"] == "praveenlavu/spec-kit-ado"
    assert load("ado-link-on-merge")["permissions"] == {"contents": "read"}


def test_azure_gate_calls_strict_mode_first_and_passes_base_to_the_id_step():
    (script,) = [s for s in _blocks(AZ_GATE) if "ado gate" in s]
    assert 'ado strict mode --base "origin/${TARGET_BRANCH#refs/heads/}" > "$AGENT_TEMPDIRECTORY/mode.json"' in script
    id_cmd = 'ado branch --id-from="$branch" --base "origin/${TARGET_BRANCH#refs/heads/}" 2> "$AGENT_TEMPDIRECTORY/id.err"'
    strict = 'env -u ADO_TOKEN ado strict check "$id" --phase pr --head "$HEAD_SHA" --base "origin/${TARGET_BRANCH#refs/heads/}" > "$AGENT_TEMPDIRECTORY/strict.json"'
    assert script.index("ado strict mode") < script.index(id_cmd) < script.index("ado gate ") < script.index(strict) < script.index('ado pr comment "$PR_ID"')
    steps_ = yaml.safe_load(AZ_GATE.read_text())["steps"]
    assert steps_[-1]["env"]["HEAD_SHA"] == "$(System.PullRequest.SourceCommitId)"
    assert all(s.get("persistCredentials") is False for s in steps_ if "checkout" in s)  # checkouts still do not persist credentials


def test_azure_link_on_merge_still_detaches_at_full_depth():
    own = next(s for s in yaml.safe_load(AZ_LINK.read_text())["steps"] if s.get("checkout") == "self")
    assert own["fetchDepth"] == 0 and own["persistCredentials"] is False


# ---- GitHub: real bash, one step at a time, with an `ado` shim ----
def _bin(tmp_path, **bodies):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    for name, body in bodies.items():
        (d / name).write_text("#!/bin/bash\n" + body)
        (d / name).chmod(0o755)
    return d


def _gh_run(tmp_path, step, env, *, no_temp=False, cwd=None, **bodies):
    """Run one step's `run:` as the runner does (bash -eo pipefail). The test must supply every key of the step's `env:` and no other:
    a token the step does not declare cannot reach the shim. Returns (exit code, output); scratch files are in tmp_path/rt."""
    assert set(env) == set(step.get("env", {})), (sorted(env), sorted(step.get("env", {})))
    (tmp_path / "rt").mkdir(exist_ok=True)
    (tmp_path / "gho").touch()
    full = {"PATH": f"{_bin(tmp_path, **bodies)}:/usr/bin:/bin", "HOME": str(tmp_path), "RUNNER_TEMP": str(tmp_path / "rt"),
            "GITHUB_OUTPUT": str(tmp_path / "gho"), "CALLS": str(tmp_path / "calls.log"), "GITHUB_REPOSITORY": "contoso/app", **env}
    if no_temp:  # the runner did not set its temp dir
        full.pop("RUNNER_TEMP")
    (tmp_path / "step.sh").write_text(step["run"])
    r = subprocess.run(["bash", *SHELL_FLAGS, str(tmp_path / "step.sh")], env=full, cwd=cwd or tmp_path, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def _calls(tmp_path):
    p = tmp_path / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


def _gho(tmp_path):
    return (tmp_path / "gho").read_text()


LOG = 'printf "%s | token=%s gh=%s\\n" "$*" "${ADO_TOKEN-unset}" "${GH_TOKEN-unset}" >> "$CALLS"\n'
MODE_ENV = {"ADO_CONFIG": "/ws/ado.config.json", "BASE_REF": "main"}
GH_GATE = WF_DIR / "ado-gate.yml"


@pytest.mark.parametrize("body,mode", [
    ('echo \'{"mode": "strict", "source": "target branch"}\'', "strict"),
    ('echo \'{"mode": "advisory", "source": "pull request"}\'', "advisory"),
    ("echo 'error: no ado.config.json' >&2; exit 2", "advisory"),  # no usable config (exit 2) stays advisory, exactly as before
])
def test_github_mode_step_outputs_the_mode_and_treats_other_failures_as_advisory(tmp_path, body, mode, shell_flags):
    rc, out = _gh_run(tmp_path, _gh("ado-gate", STEP_MODE), MODE_ENV, ado=LOG + body + "\n")
    assert rc == 0 and _gho(tmp_path) == f"mode={mode}\n", out
    assert _calls(tmp_path) == ["strict mode --base origin/main | token=unset gh=unset"]  # exactly this call, no token, and nothing else


@pytest.mark.parametrize("body", ["exit 1", "exit 4", "exit 126", "exit 127", "exit 139", None], ids=lambda b: b or "no-ado-installed")
def test_github_mode_step_fails_closed_on_every_exit_code_but_0_2_and_3(tmp_path, body, shell_flags):
    # exit 1 (a crash), 127 (no ado) and anything else: the mode is unknown, so it is never read as advisory, under ANY target
    bodies = {} if body is None else {"ado": STDOUT_ADO + STDERR_ADO + body + "\n"}
    rc, out = _gh_run(tmp_path, _gh("ado-gate", STEP_MODE), MODE_ENV, **bodies)
    assert rc == 1 and _gho(tmp_path) == "", out
    (line,) = [l for l in out.splitlines() if l.startswith("::error::")]
    assert "could not determine the strict mode" in line and (body is None or "boom" in line)
    if body is not None:
        _assert_no_injected_command(out, GH_GATE)  # the stderr is flattened by ann; nothing else of ado's reaches the log


@pytest.mark.parametrize("body", ["echo not-json", 'echo \'{"mode": "lax"}\'', "echo '{}'", "true"])
def test_github_mode_step_fails_closed_on_output_it_cannot_read(tmp_path, body, shell_flags):
    rc, out = _gh_run(tmp_path, _gh("ado-gate", STEP_MODE), MODE_ENV, ado=body + "\n")
    assert rc == 1 and "::error::" in out and _gho(tmp_path) == "", out  # exit 0 with a mode that is neither word: never a silent advisory


def test_github_mode_step_exit_3_fails_closed_with_a_sanitised_annotation(tmp_path, shell_flags):
    rc, out = _gh_run(tmp_path, _gh("ado-gate", STEP_MODE), MODE_ENV, ado=STDOUT_ADO + STDERR_ADO + "exit 3\n")
    assert rc == 3 and _gho(tmp_path) == "", out
    _assert_no_injected_command(out, GH_GATE)
    assert len([l for l in out.splitlines() if l.startswith("::error::")]) == 1


@pytest.mark.parametrize("mode,title,body,rc,ok", [
    ("strict", "AB#1001 fix", "", 0, True),
    ("strict", "no reference", "", 1, False),  # strict: the gate cannot be skipped
    ("strict", "HOSTILE-TITLE\n::set-output name=x::y", "AB#", 1, False),
    ("advisory", "no reference", "", 0, False),  # v0.2: a notice, and no gate
    ("", "no reference", "", 0, False),
])
def test_github_story_step_fails_instead_of_skipping_under_strict(tmp_path, mode, title, body, rc, ok, shell_flags):
    got, out = _gh_run(tmp_path, _gh("ado-gate", STEP_STORY), {"PR_TITLE": title, "PR_BODY": body, "MODE": mode})
    assert got == rc, out
    assert ("id=1001\n" in _gho(tmp_path)) == ok
    if rc == 1:
        assert "::error::" in out and "set-output" not in out and "HOSTILE" not in out  # the message is fixed text, never the PR's
    elif not ok:
        assert "::notice::No AB# reference in PR title/body - ADO gate skipped (advisory)." in out


FINDINGS = {"phase": "pr", "mode": "strict", "ok": False, "findings": (
    [{"rule": "red-passes", "detail": "HOSTILE-DETAIL ::error::x ##vso[task.complete result=Succeeded]", "fix": "FIXTEXT"},
     {"rule": "::error::pwn\n##vso[x]", "detail": "d", "fix": "f"}, {"rule": "a" * 100, "detail": "d", "fix": "f"}]
    + [{"rule": f"r{i}", "detail": "d", "fix": "f"} for i in range(30)])}
STRICT_ENV = {"ADO_CONFIG": "/ws/ado.config.json", "STORY_ID": "1001", "BASE_REF": "main", "HEAD_SHA": SHA}


def _findings_ado(tmp_path, doc=FINDINGS, rc=2):
    (tmp_path / "findings.json").write_text(json.dumps(doc))
    return LOG + f'cat "{tmp_path}/findings.json"\necho "error: strict check failed: boom ::error::pwned" >&2\nexit {rc}\n'


def _assert_rule_ids_only(text):
    assert "HOSTILE-DETAIL" not in text and "FIXTEXT" not in text and "task.complete" not in text, text
    (line,) = [l for l in text.splitlines() if "rule ids:" in l]
    ids = line.split("rule ids:", 1)[1].split()
    assert 0 < len(ids) <= 20 and all(re.fullmatch(r"[A-Za-z0-9_-]{1,40}", i) for i in ids), ids
    assert ids[:3] == ["red-passes", "errorpwnvsox", "a" * 40]  # sanitised: only [A-Za-z0-9_-], cut to 40, at most 20 of them


def test_github_strict_step_runs_the_exact_check_without_a_token_and_reports_rule_ids_only(tmp_path, shell_flags):
    rc, out = _gh_run(tmp_path, _gh("ado-gate", STEP_STRICT), STRICT_ENV, ado=_findings_ado(tmp_path))
    assert rc == 0, out  # the verdict is the next steps'
    assert _calls(tmp_path) == [f"strict check 1001 --phase pr --head {SHA} --base origin/main | token=unset gh=unset"]
    assert (tmp_path / "rt" / "strict.code").read_text().strip() == "2"
    _assert_rule_ids_only((tmp_path / "rt" / "strict.md").read_text())
    assert (tmp_path / "rt" / "strict.rules").read_text().split()[:1] == ["red-passes"]
    assert "HOSTILE-DETAIL" not in out and "task.complete" not in out  # ado's stdout (the findings) never reaches the log
    for line in out.splitlines():
        assert not line.startswith(("::error::pwned", "::set-output")), repr(line)
    assert "boom" in out  # the CLI's own stderr line is there, flattened by ann


def test_github_strict_step_passes_cleanly(tmp_path, shell_flags):
    rc, out = _gh_run(tmp_path, _gh("ado-gate", STEP_STRICT), STRICT_ENV, ado=LOG + 'echo \'{"ok": true, "findings": []}\'\n')
    assert rc == 0 and (tmp_path / "rt" / "strict.code").read_text().strip() == "0" and not (tmp_path / "rt" / "strict.md").exists(), out


@pytest.mark.parametrize("body", ["exit 1", "exit 127", "echo '{}'; exit 2", "echo not-json; exit 2"])
def test_github_strict_step_failure_without_findings_still_fails_the_job_later(tmp_path, body, shell_flags):
    (tmp_path / "rt").mkdir()
    (tmp_path / "rt" / "strict.md").write_text("STALE")  # a stale file must never be mistaken for this run's
    rc, out = _gh_run(tmp_path, _gh("ado-gate", STEP_STRICT), STRICT_ENV, ado=LOG + body + "\n")
    assert rc == 0 and (tmp_path / "rt" / "strict.code").read_text().strip() not in ("", "0"), out
    assert "STALE" not in (tmp_path / "rt" / "strict.md").read_text() and "could not run" in (tmp_path / "rt" / "strict.md").read_text()


@pytest.mark.parametrize("code,rules,rc", [("0", "", 0), ("2", "red-passes l2", 1), ("1", "", 1), (None, "", 1), ("garbage", "", 1)])
def test_github_enforce_step_fails_on_any_strict_failure(tmp_path, code, rules, rc, shell_flags):
    (tmp_path / "rt").mkdir()
    if code is not None:
        (tmp_path / "rt" / "strict.code").write_text(code + "\n")
    (tmp_path / "rt" / "strict.rules").write_text(rules + "\n")
    got, out = _gh_run(tmp_path, _gh("ado-gate", STEP_ENFORCE), {})
    assert got == rc, out
    if rc:
        assert "::error::spec-kit-ado: strict check failed" in out and (not rules or f"rule ids: {rules}" in out)
    else:
        assert "::error::" not in out


def test_github_post_step_adds_the_rule_ids_to_the_comment_only_under_strict(tmp_path, shell_flags):
    (tmp_path / "rt").mkdir()
    (tmp_path / "rt" / "gate.md").write_text("GATE-BODY\n")
    (tmp_path / "rt" / "gate.code").write_text("0\n")
    (tmp_path / "rt" / "strict.md").write_text("\nSTRICT-LINE rule ids: red-passes\n")
    gh = 'case "$*" in "pr comment"*) cp "$5" "$POSTED";; esac\nexit 0\n'
    for mode in ("strict", "advisory", ""):
        posted = tmp_path / f"posted-{mode or 'unset'}.md"
        rc, out = _gh_run(tmp_path, _gh("ado-gate", STEP_POST), {"GH_TOKEN": "t", "PR_NUM": "7", "MODE": mode}, gh=gh.replace("$POSTED", str(posted)))
        assert rc == 0, out
        text = posted.read_text()
        assert text.startswith("<!-- spec-kit-ado:gate -->\nGATE-BODY\n") and ("STRICT-LINE rule ids: red-passes" in text) == (mode == "strict"), (mode, text)


# ---- Azure gate: the whole script, real bash, an `ado` shim ----
def _az_shim(*, mode=("strict", 0), id_rc=0, gate_rc=0, strict_rc=2, strict_doc=FINDINGS, mode_err="", mode_out=None, blocking=False):
    mode_name, mode_rc = mode
    mode_json = mode_out if mode_out is not None else json.dumps({"mode": mode_name, "source": "target branch"})
    return ('echo "$* | token=${ADO_TOKEN:+set}" >> calls.log\n'
            'case "$1 $2" in\n'
            f' "strict mode") echo \'{mode_json}\'; {"echo " + repr(mode_err) + " >&2; " if mode_err else ""}exit {mode_rc};;\n'
            f' "branch --id-from="*|"branch --id-from") [ {id_rc} = 0 ] && echo 1001; exit {id_rc};;\n'
            f' "strict check") cat strict-doc.json; echo "error: strict check failed: boom" >&2; exit {strict_rc};;\n'
            ' "pr comment") cp "$5" posted.md; exit 0;;\n'
            f' "gate 1001") echo \'{{"blocking": {"true" if blocking else "false"}}}\'; exit {gate_rc};;\n'
            'esac\nexit 0\n')


def _run_az(tmp_path, **kw):
    (tmp_path / "strict-doc.json").write_text(json.dumps(kw.pop("strict_doc", FINDINGS)))
    shim = _az_shim(**kw)
    rc, out, _ = _run_gate_step(tmp_path, AZ_GATE, {}, ado_body=shim, extra_env={"ADO_TOKEN": "tok-secret"}, flags=SHELL_FLAGS)
    return rc, out


def test_azure_gate_strict_target_without_a_story_id_fails_instead_of_skipping(tmp_path, shell_flags):
    rc, out = _run_az(tmp_path, id_rc=1)
    assert rc == 1 and "##vso[task.logissue type=error]ADO gate FAILED: strict mode needs a work item id" in out, out
    assert [c.split(" |")[0] for c in _calls(tmp_path)][:2] == ["strict mode --base origin/main", "branch --id-from=feature/ab1001-x --base origin/main"]
    assert not any(c.startswith(("gate", "pr comment", "strict check")) for c in _calls(tmp_path)) and "skipped" not in out


def test_azure_gate_advisory_without_a_story_id_still_skips(tmp_path, shell_flags):
    rc, out = _run_az(tmp_path, mode=("advisory", 0), id_rc=1)
    assert rc == 0 and "##vso[task.logissue type=warning]No work item id in the source branch - ADO gate skipped (advisory)." in out, out
    assert not any(c.startswith(("gate", "pr comment", "strict check")) for c in _calls(tmp_path))


@pytest.mark.parametrize("id_rc", [2, 127])
def test_azure_gate_strict_target_cannot_read_the_id_fails_closed_and_advisory_skips(tmp_path, id_rc, shell_flags):
    assert _run_az(tmp_path, id_rc=id_rc)[0] == 1
    other = tmp_path / "adv"
    other.mkdir()
    rc, out = _run_az(other, mode=("advisory", 0), id_rc=id_rc)
    assert rc == 0 and "skipped (advisory)" in out


def test_azure_gate_strict_check_runs_after_the_gate_without_the_token_and_lists_rule_ids_only(tmp_path, shell_flags):
    rc, out = _run_az(tmp_path)
    assert rc == 1, out
    calls = _calls(tmp_path)
    assert [c.split(" ")[0] + " " + c.split(" ")[1] for c in calls] == ["strict mode", "branch --id-from=feature/ab1001-x", "gate 1001", "strict check", "pr comment"]
    assert calls[2].endswith("token=set") and calls[4].endswith("token=set")  # the gate and the comment keep the token
    assert calls[3] == f"strict check 1001 --phase pr --head {SHA} --base origin/main | token="  # and the strict check does not
    posted = (tmp_path / "posted.md").read_text()
    _assert_rule_ids_only(posted)
    (err,) = [l for l in out.splitlines() if l.startswith("##vso[task.logissue type=error]")]
    _assert_rule_ids_only(err)
    assert "HOSTILE-DETAIL" not in out and "task.complete" not in out.replace("##vso[task.logissue", "")
    _assert_no_injected_command(out, AZ_GATE)


def test_azure_gate_strict_check_passes(tmp_path, shell_flags):
    rc, out = _run_az(tmp_path, strict_doc={"ok": True, "findings": []}, strict_rc=0)
    assert rc == 0 and "strict check failed" not in (tmp_path / "posted.md").read_text() and "##vso[task.logissue type=error]" not in out, out


@pytest.mark.parametrize("strict_rc,doc", [(1, {}), (127, {}), (2, {"nothing": 1})])
def test_azure_gate_strict_check_that_cannot_run_fails_closed(tmp_path, strict_rc, doc, shell_flags):
    rc, out = _run_az(tmp_path, strict_rc=strict_rc, strict_doc=doc)
    assert rc == 1 and "none" in (tmp_path / "posted.md").read_text(), out


def test_azure_gate_strict_check_failure_wins_over_a_clean_gate_but_a_blocking_gate_still_fails(tmp_path, shell_flags):
    assert _run_az(tmp_path, gate_rc=0)[0] == 1
    other = tmp_path / "adv"
    other.mkdir()
    rc, out = _run_az(other, mode=("advisory", 0), strict_rc=2)  # advisory: the strict check never runs, whatever it would say
    assert rc == 0 and not any(c.startswith("strict check") for c in _calls(other)), out


def test_azure_gate_mode_exit_3_fails_closed_before_anything_else(tmp_path, shell_flags):
    rc, out = _run_az(tmp_path, mode=("strict", 3), mode_err="error: target branch config: boom ##vso[task.complete result=Succeeded]")
    assert rc == 1 and "##vso[task.logissue type=error]ADO gate FAILED: ado.config.json is invalid" in out and out.count("##vso[") == 1, out
    assert len(_calls(tmp_path)) == 1  # nothing after the mode call


@pytest.mark.parametrize("mode_rc", [1, 4, 126, 127, 139])
def test_azure_gate_mode_fails_closed_on_every_exit_code_but_0_2_and_3(tmp_path, mode_rc, shell_flags):
    rc, out = _run_az(tmp_path, mode=("advisory", mode_rc), mode_err="error: boom ##vso[task.complete result=Succeeded]")
    assert rc == 1 and "##vso[task.logissue type=error]ADO gate FAILED: could not determine the strict mode" in out and "boom" in out, out
    assert out.count("##vso[") == 1 and len(_calls(tmp_path)) == 1  # one annotation of our own; nothing runs after the mode call


def test_azure_gate_mode_exit_2_is_advisory_as_before(tmp_path, shell_flags):
    rc, out = _run_az(tmp_path, mode=("advisory", 2))
    assert rc == 0 and any(c.startswith("gate 1001") for c in _calls(tmp_path)) and not any(c.startswith("strict check") for c in _calls(tmp_path)), out


@pytest.mark.parametrize("mode_out", ["not json", '{"mode": "lax"}', "{}"])
def test_azure_gate_mode_it_cannot_read_fails_closed(tmp_path, mode_out, shell_flags):
    rc, out = _run_az(tmp_path, mode_out=mode_out)
    assert rc == 1 and "##vso[task.logissue type=error]ADO gate FAILED: could not read the strict mode" in out, out


# ---- link-on-merge: a strict done refusal is reported by rule id and fails the job ----
def _refused_ado(tmp_path, doc=FINDINGS, plan_rc=2, stderr="error: strict check failed: 2 finding(s)"):
    (tmp_path / "refusal.json").write_text(json.dumps(doc))
    return ('echo "$*" >> calls.log\n'
            'case "$*" in\n'
            f' *--plan-out*) cat refusal.json; echo "{stderr}" >&2; exit {plan_rc};;\n'
            'esac\nexit 0\n')


def _link_run(tmp_path, path, ado, *, ids="7", state=""):
    _link_with_ledger(tmp_path)
    (tmp_path / "dod" / "DoD_8.md").write_text("x")
    snippet = f"export IDS='{ids}' CLOSE_STORY=true STATE={state} PR_NUM=1 PR_URL=u BRANCH=main SOURCE_VERSION=abcdef123456\n" + _link_snippet(path)
    snippet = snippet.replace("\nids=7\n", f"\nids='{ids}'\n", 1)  # the Azure template reads its ids from the merge commit message
    return _run_snippet(tmp_path, snippet, "main", ado_body=ado, flags=SHELL_FLAGS)


ERR_PREFIX = {"github": "::error::", "azure": "##vso[task.logissue type=error]"}


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_reports_a_strict_refusal_by_rule_id_and_fails_the_job(tmp_path, path, shell_flags):
    family = "azure" if path.parent.name == "pipelines" else "github"
    rc, out = _link_run(tmp_path, path, _refused_ado(tmp_path), ids="7 8")
    assert rc == 1, out
    lines = [l for l in out.splitlines() if l.startswith(ERR_PREFIX[family]) and "strict done refused" in l]
    assert len(lines) == 2 and lines[0].startswith(f"{ERR_PREFIX[family]}7: strict done refused: ") and "8: strict done refused" in lines[1], out
    for line in lines:
        _assert_rule_ids_only(line.replace("strict done refused: ", "rule ids: "))
    assert "check ADO_TOKEN" not in out and "could not evaluate" not in out  # the misleading advisory warning is not for a refusal
    calls = _calls(tmp_path)
    assert not any("--apply" in c for c in calls) and [c for c in calls if c.startswith("comment")]  # nothing attached; the merge note still posted
    assert "HOSTILE-DETAIL" not in out


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_a_refusal_does_not_stop_the_other_stories_from_closing(tmp_path, path, shell_flags):
    ado = ('echo "$*" >> calls.log\ncase "$*" in\n'
           ' "done 7 --plan-out"*) cat refusal.json; exit 2;;\n'
           ' "done 8 --plan-out"*) echo \'{"ready": true}\'; exit 0;;\n'
           ' "done 8 --apply"*) echo \'{"attached": [{"path": "p"}], "failed": [], "state": "Done"}\'; exit 0;;\nesac\nexit 0\n')
    (tmp_path / "refusal.json").write_text(json.dumps(FINDINGS))
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_8.md").write_text("x")
    rc, out = _link_run(tmp_path, path, ado, ids="7 8")
    assert rc == 1 and "8: DoD complete - ledger attached, story closed." in out and "7: strict done refused" in out, out


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
@pytest.mark.parametrize("doc", [{"error": "x"}, [], "text"])
def test_link_on_merge_only_the_refusal_shape_is_a_refusal(tmp_path, path, doc, shell_flags):
    # exit 2 with something else on stdout (or none) is the v0.2 "could not evaluate" path: a warning, never a failure
    rc, out = _link_run(tmp_path, path, _refused_ado(tmp_path, doc=doc))
    assert rc == 0 and "could not evaluate the DoD ledger" in out and "strict done refused" not in out, out


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_refusal_with_hostile_rule_ids_stays_one_line_of_ids(tmp_path, path, shell_flags):
    doc = {"phase": "done", "mode": "strict", "ok": False,
           "findings": [{"rule": "::error::pwn ##vso[task.complete result=Succeeded]"}, {"rule": "x" * 500}, {"rule": 5}, {"rule": "‮\x1b[31mbidi"}]}
    rc, out = _link_run(tmp_path, path, _refused_ado(tmp_path, doc=doc))
    assert rc == 1
    (line,) = [l for l in out.splitlines() if "strict done refused" in l]
    ids = line.split("strict done refused: ", 1)[1].split()
    assert ids == ["errorpwnvsotaskcompleteresultSucceeded", "x" * 40, "5", "31mbidi"] and not re.search(r"[^\x20-\x7e]", line), repr(line)


WARN_PREFIX = {"github": "::warning::", "azure": "##vso[task.logissue type=warning]"}


def _signoff_doc(*rules):
    return {"phase": "done", "mode": "advisory", "ok": False, "findings": [{"rule": r, "fix": "HOSTILE-DETAIL"} for r in rules], "overrides_used": []}


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_a_missing_signoff_is_awaiting_not_a_failure(tmp_path, path, shell_flags):
    family = "azure" if path.parent.name == "pipelines" else "github"
    rc, out = _link_run(tmp_path, path, _refused_ado(tmp_path, doc=_signoff_doc("signoff-bpo")), ids="7 8")
    assert rc == 0, out  # the job stays green
    lines = [l for l in out.splitlines() if "awaiting" in l]
    assert len(lines) == 2 and lines[0].startswith(f"{WARN_PREFIX[family]}7: awaiting signoff-bpo - a human closes the story with ado done 7") and "8: awaiting signoff-bpo" in lines[1], out
    assert "strict done refused" not in out and "could not evaluate" not in out and "HOSTILE-DETAIL" not in out
    assert not any("--apply" in c for c in _calls(tmp_path))  # nothing attached, nothing closed


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_awaiting_story_is_not_transitioned_either(tmp_path, path, shell_flags):
    # a story closed by hand must not be moved back to `transitionState`, and a transition to the done state must not close it without its sign-offs
    rc, out = _link_run(tmp_path, path, _refused_ado(tmp_path, doc=_signoff_doc("signoff-bpo")), state="Resolved")
    assert rc == 0 and "awaiting signoff-bpo" in out, out
    assert not [c for c in _calls(tmp_path) if c.startswith("update")], _calls(tmp_path)


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_awaiting_names_every_missing_role(tmp_path, path, shell_flags):
    rc, out = _link_run(tmp_path, path, _refused_ado(tmp_path, doc=_signoff_doc("signoff-tpo", "signoff-qa", "signoff-bpo")))
    assert rc == 0 and "7: awaiting signoff-tpo signoff-qa signoff-bpo - " in out, out


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
@pytest.mark.parametrize("rules", [("signoff-bypass",), ("signoff-unreadable",), ("signoff-terminal",), ("signoff-trust",), ("signoff-bpo", "signoff-bypass"),
                                   ("signoff-bpo", "decisions-xcheck-verdict"), ("signoff-bpo\n::error::x",)])
def test_link_on_merge_only_a_pure_awaiting_list_is_awaiting(tmp_path, path, rules, shell_flags):
    # a refusal of the whole gate (bypass token, unreadable thread, terminal mode) or any other rule is the strict refusal: red
    rc, out = _link_run(tmp_path, path, _refused_ado(tmp_path, doc=_signoff_doc(*rules)))
    assert rc == 1 and "strict done refused" in out and "awaiting" not in out, out


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_a_result_with_ready_is_never_a_refusal(tmp_path, path, shell_flags):
    rc, out = _link_run(tmp_path, path, _refused_ado(tmp_path, doc={"ready": False, "findings": [{"rule": "x"}]}, plan_rc=1))
    assert rc == 0 and "DoD not ready" in out and "strict done refused" not in out, out


def test_github_mode_step_with_the_real_cli_follows_the_target_branch(tmp_path, shell_flags):
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a], cwd=tmp_path / "ws", check=True, capture_output=True)
    (tmp_path / "ws").mkdir()
    org = {"organization": "contoso", "project": "Fabrikam"}
    git("init", "-q", "-b", "main")
    (tmp_path / "ws" / "ado.config.json").write_text(json.dumps({**org, "mode": "strict"}))
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    (tmp_path / "ws" / "ado.config.json").write_text(json.dumps(org))  # the PR says advisory
    real = f'exec "{sys.executable}" -c \'{ADO_CLI}\' "$@"\n'
    env = {"ADO_CONFIG": str(tmp_path / "ws" / "ado.config.json"), "BASE_REF": "main", "PYTHONPATH": str(ROOT / "src")}
    step = dict(_gh("ado-gate", STEP_MODE), env={**_gh("ado-gate", STEP_MODE)["env"], "PYTHONPATH": ""})
    rc, out = _gh_run(tmp_path, step, env, cwd=tmp_path / "ws", ado=real)
    assert rc == 0 and _gho(tmp_path) == "mode=strict\n", out
    (tmp_path / "gho").write_text("")
    rc, out = _gh_run(tmp_path, step, {**env, "BASE_REF": "nosuch"}, cwd=tmp_path / "ws", ado=real)  # a base this checkout lacks, an advisory PR config: the v0.2 path
    assert rc == 0 and _gho(tmp_path) == "mode=advisory\n", out
    (tmp_path / "ws" / "ado.config.json").write_text(json.dumps({**org, "mode": "strict"}))
    (tmp_path / "gho").write_text("")
    rc, out = _gh_run(tmp_path, step, {**env, "BASE_REF": "nosuch"}, cwd=tmp_path / "ws", ado=real)  # ... and with a strict PR config it fails closed
    assert rc == 3 and _gho(tmp_path) == "" and "::error::" in out, out


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_a_refused_story_is_never_transitioned_the_others_are(tmp_path, path, shell_flags):
    ado = ('echo "$*" >> calls.log\ncase "$*" in\n'
           ' "done 7 --plan-out"*) cat refusal.json; echo "error: strict check failed: 2 finding(s)" >&2; exit 2;;\n'
           ' "done 8 --plan-out"*) echo \'{"ready": false}\'; exit 1;;\n'
           'esac\nexit 0\n')
    (tmp_path / "refusal.json").write_text(json.dumps(FINDINGS))
    rc, out = _link_run(tmp_path, path, ado, ids="7 8", state="Resolved")
    assert rc == 1 and "7: strict done refused" in out and "8: DoD not ready" in out, out  # the job still fails at the end
    calls = _calls(tmp_path)
    assert "update 8 --state Resolved" in calls and not any(c.startswith("update 7") for c in calls), calls  # only 8 transitions
    assert any(c.startswith("comment 7") for c in calls) and any(c.startswith("comment 8") for c in calls)  # both merge notes are posted


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_a_refused_story_is_not_moved_to_done_state_even_when_asked(tmp_path, path, shell_flags):
    rc, out = _link_run(tmp_path, path, _refused_ado(tmp_path), state="Done")  # transition-state == done_state: the worst case
    assert rc == 1 and not any(c.startswith("update") for c in _calls(tmp_path)), _calls(tmp_path)


# ======================================================================================================================
# V3-14b: the optional secret-free CI re-run job (`ado strict verify --no-record`), shipped UNRUN (not yet run in a consumer repository): structural pins,
# `bash -n` (the parametrised test above covers both new files) and real-bash shim tests only.
# ======================================================================================================================
VERIFY_WF = "ado-strict-verify"
VERIFY_GH = WF_DIR / "ado-strict-verify.yml"
VERIFY_AZ = ROOT / "pipelines" / "ado-strict-verify.yml"
VERIFY_IF = "steps.mode.outputs.rerun == 'yes'"
VERIFY_MODE_ENV = {"BASE_REF": "main", "EVENT_NAME": "pull_request"}
VERIFY_STEP_ENV = {"STORY_ID": "1001", "BASE_REF": "main", "ALLOW_OVERRIDE": "false"}
VERIFY_CMD_GH = 'ado strict verify "$STORY_ID" --no-record --base "origin/${BASE_REF}" $allow'
VERIFY_CMD_AZ = 'env -u ADO_TOKEN ado strict verify "$id" --no-record --base "origin/${TARGET_BRANCH#refs/heads/}" $allow'
STRICT_TARGET = '{"mode": "strict", "source": "target branch"}'
FORBIDDEN = ("ADO_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "System.AccessToken", "SYSTEM_ACCESSTOKEN", "secrets.", "github.token", "pull_request_target")


@pytest.mark.parametrize("path", [VERIFY_GH, VERIFY_AZ], ids=["github", "azure"])
def test_verify_templates_hold_no_secret_and_never_the_privileged_trigger(path):
    text = path.read_text()
    text = text.replace("env -u ADO_TOKEN ado strict verify ", "")  # the one allowed mention: the Azure step drops a token it never maps, as the gate does
    for word in FORBIDDEN:  # the whole file, comments included: nothing here may even name a token or the privileged event
        assert word not in text, (path.name, word)
    assert not re.search(r"(^|\s)token:", text) and "--record" not in text.replace("--no-record", "")


def test_github_verify_workflow_is_secret_free_and_least_privilege():
    wf = load(VERIFY_WF)
    call = triggers(wf)
    assert list(call) == ["workflow_call"] and list(call["workflow_call"]) == ["inputs"]  # no `secrets:` block at all
    assert list(call["workflow_call"]["inputs"]) == ["toolkit-ref", "allow-override"] and call["workflow_call"]["inputs"]["toolkit-ref"]["default"] == "v0"
    assert call["workflow_call"]["inputs"]["allow-override"]["type"] == "boolean" and call["workflow_call"]["inputs"]["allow-override"]["default"] is False
    assert wf["permissions"] == {"contents": "read"} and "env" not in wf
    (job,) = wf["jobs"].values()
    assert "env" not in job and "permissions" not in job and "secrets" not in job and "needs" not in job
    checkouts = [s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout")]
    assert len(checkouts) == 2 and all(c["with"]["persist-credentials"] is False for c in checkouts)  # every checkout
    assert checkouts[0]["with"] == {"ref": "${{ github.event.pull_request.head.sha }}", "fetch-depth": 0, "persist-credentials": False}  # the PR head, not the merge commit
    assert checkouts[1]["with"] == {"repository": "praveenlavu/spec-kit-ado", "ref": "${{ inputs.toolkit-ref }}", "path": ".ado-toolkit",
                                    "persist-credentials": False}  # no `token:`: the default checkout credential, never persisted
    for s in job["steps"]:
        assert "env" not in s or not set(s["env"]) & {"ADO_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"}


def test_github_verify_steps_run_in_order_and_use_exactly_the_verify_command():
    names = [(s.get("id"), s["name"]) for s in steps(load(VERIFY_WF)) if "run" in s]
    assert [i for i, _ in names] == [None, "mode", "story", "verify"]  # install, mode FIRST, story id, the re-run
    mode, story, verify = (_gh(VERIFY_WF, k) for k in ("mode", "story", "verify"))
    assert set(mode["env"]) == {"BASE_REF", "EVENT_NAME"} and mode["env"]["EVENT_NAME"] == "${{ github.event_name }}"
    assert 'ado strict mode --base "origin/${BASE_REF}" > "$RUNNER_TEMP/mode.json"' in mode["run"]
    assert "if" not in mode and story["if"] == VERIFY_IF and verify["if"] == VERIFY_IF
    assert set(story["env"]) == {"PR_TITLE", "PR_BODY"} and set(verify["env"]) == {"STORY_ID", "BASE_REF", "ALLOW_OVERRIDE"}
    assert verify["env"]["STORY_ID"] == "${{ steps.story.outputs.id }}" and verify["env"]["BASE_REF"] == "${{ github.base_ref }}"
    assert VERIFY_CMD_GH in verify["run"] and len(re.findall(r"(?m)^\s*ado strict verify ", verify["run"])) == 1  # one call, nothing else
    assert not re.search(r"(?m)^\s*ado strict verify ", mode["run"] + story["run"])  # only the last step runs the project's tests
    assert verify["env"]["ALLOW_OVERRIDE"] == "${{ inputs.allow-override }}"
    assert 'ado strict verify "$STORY_ID" --no-record --base "origin/${BASE_REF}" $allow >' in verify["run"]  # only the flag is appended


def test_github_verify_guards_and_story_id_rule_match_the_gate():
    guard = ': "${RUNNER_TEMP:?RUNNER_TEMP is not set - refusing to write scratch files into the checkout}"'
    for key in ("mode", "verify"):
        assert _gh(VERIFY_WF, key)["run"].splitlines()[0] == guard
    assert ': "${STORY_ID:?' in _gh(VERIFY_WF, "verify")["run"] and ': "${BASE_REF:?' in _gh(VERIFY_WF, "verify")["run"]
    assert ': "${BASE_REF:?' in _gh(VERIFY_WF, "mode")["run"]
    gate_id = next(l for l in _gh("ado-gate", "story")["run"].splitlines() if l.startswith("id=$("))
    assert gate_id in _gh(VERIFY_WF, "story")["run"]  # the same first-AB#-token rule, copied byte for byte
    assert "$PR_TITLE" in _gh(VERIFY_WF, "story")["run"] and "${{" not in "".join(s.get("run", "") for s in steps(load(VERIFY_WF)))


def _v_ado(tmp_path, *, mode=STRICT_TARGET, mode_rc=0, id_rc=0, verify_rc=0, verify_doc=FINDINGS):
    """A fake `ado` for the re-run job (GitHub: `strict mode`, `strict verify`; Azure also `branch --id-from=`). It logs every call."""
    (tmp_path / "mode.json").write_text(mode)
    (tmp_path / "verify.json").write_text(json.dumps(verify_doc))
    return (LOG + 'case "$1 $2" in\n'
            f' "strict mode") cat "{tmp_path}/mode.json"; echo "error: mode boom ::error::pwned" >&2; exit {mode_rc};;\n'
            f' "branch --id-from="*) [ {id_rc} = 0 ] && echo 1001; exit {id_rc};;\n'
            f' "strict verify") cat "{tmp_path}/verify.json"; echo "error: strict verify failed: boom ::error::pwned" >&2; exit {verify_rc};;\n'
            'esac\nexit 0\n')


def _verify_job(tmp_path, ado, *, title="AB#1001 fix", body="", event="pull_request", allow="false"):
    """The three GitHub steps in order, sharing one GITHUB_OUTPUT, with the `if:` of the later steps applied (pinned above)."""
    rc, out = _gh_run(tmp_path, _gh(VERIFY_WF, "mode"), {**VERIFY_MODE_ENV, "EVENT_NAME": event}, ado=ado)
    outputs = lambda: dict(l.split("=", 1) for l in _gho(tmp_path).splitlines())
    if rc or outputs().get("rerun") != "yes":
        return rc, out
    rc, o = _gh_run(tmp_path, _gh(VERIFY_WF, "story"), {"PR_TITLE": title, "PR_BODY": body}, ado=ado)
    out += o
    if rc:
        return rc, out
    rc, o = _gh_run(tmp_path, _gh(VERIFY_WF, "verify"), {"STORY_ID": outputs()["id"], "BASE_REF": "main", "ALLOW_OVERRIDE": allow}, ado=ado)
    return rc, out + o


def _run_az_verify(tmp_path, ado, *, branch="feature/ab1001-x", target="refs/heads/main", temp=True, with_ado=True, head_sha=SHA, git_rc=0,
                   extra_env=None, allow="False"):
    """The Azure template's one script, whole, as the agent runs it (`bash --noprofile --norc`, no -e unless a test asks)."""
    step = next(s for s in yaml.safe_load(VERIFY_AZ.read_text())["steps"] if "strict verify" in s.get("script", ""))
    assert set(step["env"]) == {"SOURCE_BRANCH", "TARGET_BRANCH", "HEAD_SHA", "ALLOW_OVERRIDE"}  # no token of any kind
    (tmp_path / "at").mkdir(exist_ok=True)
    git = f'echo "$*" >> "{tmp_path}/git.log"\nexit {git_rc}\n'  # a fake git: the template's checkout of the PR head, logged apart from the `ado` calls
    env = {"PATH": f"{_bin(tmp_path, git=git, **({'ado': ado} if with_ado else {}))}:/usr/bin:/bin", "HOME": str(tmp_path), "CALLS": str(tmp_path / "calls.log"),
           "SOURCE_BRANCH": branch, "TARGET_BRANCH": target, "HEAD_SHA": head_sha, "ALLOW_OVERRIDE": allow, **(extra_env or {})}
    if temp:
        env["AGENT_TEMPDIRECTORY"] = str(tmp_path / "at")
    (tmp_path / "step.sh").write_text(step["script"])
    r = subprocess.run(["bash", *SHELL_FLAGS, str(tmp_path / "step.sh")], env=env, cwd=tmp_path, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def _rules_ok(out):
    _assert_rule_ids_only(out)
    assert "HOSTILE-DETAIL" not in out and "task.complete" not in out.replace("##vso[task.logissue", "")  # ado's stdout (the findings) is never logged
    assert "pwned" not in "".join(l for l in out.splitlines() if l.startswith(("::error::pwn", "::set-output")))


JOBS = {"github": lambda d, ado, **kw: _verify_job(d, ado, **kw), "azure": lambda d, ado, **kw: _run_az_verify(d, ado, **kw)}


@pytest.fixture(params=list(JOBS))
def job(request):
    return request.param, JOBS[request.param]


CALL_MODE = "strict mode --base origin/main | token=unset gh=unset"
CALL_VERIFY = "strict verify 1001 --no-record --base origin/main | token=unset gh=unset"


def _verify_calls(tmp_path, kind):
    return [c for c in _calls(tmp_path) if not c.startswith("branch ")] if kind == "azure" else _calls(tmp_path)


def test_verify_job_strict_target_runs_the_exact_command_and_passes(tmp_path, job, shell_flags):
    kind, run = job
    rc, out = run(tmp_path, _v_ado(tmp_path, verify_doc={"ok": True, "findings": []}))
    assert rc == 0 and not [l for l in out.splitlines() if l.startswith("::error::")] and "type=error" not in out, out
    assert _verify_calls(tmp_path, kind) == [CALL_MODE, CALL_VERIFY]  # no token reached either call, and nothing else ran


@pytest.mark.parametrize("allow,flag", [("false", ""), ("False", ""), ("", ""), ("true", " --allow-override"), ("True", " --allow-override")])
def test_verify_job_adds_allow_override_only_when_asked(tmp_path, job, allow, flag, shell_flags):
    kind, run = job
    rc, out = run(tmp_path, _v_ado(tmp_path, verify_doc={"ok": True, "findings": []}), allow=allow)
    assert rc == 0, out
    assert _verify_calls(tmp_path, kind) == [CALL_MODE, CALL_VERIFY.replace(" | ", f"{flag} | ", 1)]


def test_verify_job_exit_2_fails_the_job_and_lists_sanitised_rule_ids_only(tmp_path, job, shell_flags):
    kind, run = job
    rc, out = run(tmp_path, _v_ado(tmp_path, verify_rc=2))
    assert rc == 1, out
    _rules_ok(out)
    assert "exit 2" in out and "boom" in out  # the CLI's own stderr line, flattened by ann
    _assert_no_injected_command(out, VERIFY_GH if kind == "github" else VERIFY_AZ)
    assert _verify_calls(tmp_path, kind) == [CALL_MODE, CALL_VERIFY]


@pytest.mark.parametrize("rc_in,doc", [(1, {}), (127, {}), (139, {"nothing": 1}), (2, {"findings": "no"}), (2, {})])
def test_verify_job_fails_closed_on_any_non_zero_exit_even_without_findings(tmp_path, job, rc_in, doc, shell_flags):
    kind, run = job
    rc, out = run(tmp_path, _v_ado(tmp_path, verify_rc=rc_in, verify_doc=doc))
    assert rc == 1 and f"exit {rc_in}" in out and "none" in out, out


def test_verify_job_strict_target_without_a_story_id_fails_and_never_runs_the_tests(tmp_path, job, shell_flags):
    kind, run = job
    kw = {"branch": "feature/no-id"} if kind == "azure" else {"title": "HOSTILE-TITLE ::set-output name=x::y", "body": "no ref"}
    rc, out = run(tmp_path, _v_ado(tmp_path, id_rc=1), **kw)
    assert rc == 1 and ("::error::" in out or "type=error" in out) and "HOSTILE" not in out and "set-output" not in out, out  # fixed text
    assert not any(c.startswith("strict verify") for c in _calls(tmp_path))


@pytest.mark.parametrize("mode", [
    '{"mode": "advisory", "source": "pull request"}',      # the target is advisory: the test command would come from the PR
    '{"mode": "strict", "source": "pull request"}',        # only the PR says strict: still the PR's command
])
def test_verify_job_runs_nothing_unless_the_target_branch_is_strict(tmp_path, job, mode, shell_flags):
    kind, run = job
    rc, out = run(tmp_path, _v_ado(tmp_path, mode=mode))
    assert rc == 0 and "skipped" in out and "type=error" not in out and "::error::" not in out, out
    assert _calls(tmp_path) == [CALL_MODE]  # no story-id call, no verify: nothing of the project ran
    if kind == "github":
        assert _gho(tmp_path) == "rerun=no\n"


def test_verify_job_no_usable_config_exit_2_is_advisory_and_does_nothing(tmp_path, job, shell_flags):
    kind, run = job
    rc, out = run(tmp_path, _v_ado(tmp_path, mode="", mode_rc=2))
    assert rc == 0 and "skipped" in out and _calls(tmp_path) == [CALL_MODE], out


def test_verify_job_mode_exit_3_fails_closed(tmp_path, job, shell_flags):
    kind, run = job
    rc, out = run(tmp_path, _v_ado(tmp_path, mode="", mode_rc=3))
    assert rc == (3 if kind == "github" else 1), out
    assert ("::error::" in out or "type=error" in out) and "boom" in out
    _assert_no_injected_command(out, VERIFY_GH if kind == "github" else VERIFY_AZ)
    assert _calls(tmp_path) == [CALL_MODE]


@pytest.mark.parametrize("rc_in", [1, 4, 126, 127, 139])
def test_verify_job_mode_failure_other_than_2_or_3_fails_closed(tmp_path, job, rc_in, shell_flags):
    kind, run = job
    rc, out = run(tmp_path, _v_ado(tmp_path, mode="", mode_rc=rc_in))
    assert rc == 1 and "could not determine the strict mode" in out and _calls(tmp_path) == [CALL_MODE], out


def test_verify_job_without_ado_installed_fails_closed(tmp_path, job, shell_flags):
    kind, run = job
    rc, out = _gh_run(tmp_path, _gh(VERIFY_WF, "mode"), VERIFY_MODE_ENV) if kind == "github" else _run_az_verify(tmp_path, None, with_ado=False)
    assert rc == 1 and "could not determine the strict mode" in out, out


@pytest.mark.parametrize("mode", ["not json", '{"mode": "lax", "source": "target branch"}', "{}", '{"mode": "strict"}', "",
                                  '{"mode": "strict\\n", "source": "target branch"}', '{"mode": "advisory", "source": "target branch"}'])
def test_verify_job_unreadable_mode_output_fails_closed(tmp_path, job, mode, shell_flags):
    kind, run = job
    rc, out = run(tmp_path, _v_ado(tmp_path, mode=mode))
    assert rc == 1 and ("::error::" in out or "type=error" in out) and _calls(tmp_path) == [CALL_MODE], out


@pytest.mark.parametrize("event", ["pull_request_target", "push", "workflow_dispatch", ""])
def test_github_verify_refuses_any_event_but_pull_request(tmp_path, event, shell_flags):
    rc, out = _verify_job(tmp_path, _v_ado(tmp_path), event=event)
    assert rc == 1 and "pull_request event only" in out and _calls(tmp_path) == [], out  # before any ado call


def test_verify_story_id_is_the_first_whole_ab_token(tmp_path, shell_flags):
    for title, body, want in (("fooAB#9 and AB#1001", "AB#2", "id=1001"), ("x", "see AB#77, AB#78", "id=77"), ("ab#5 AB#0 AB#", "", None)):
        d = tmp_path / f"c{len(os.listdir(tmp_path))}"
        d.mkdir()
        rc, out = _gh_run(d, _gh(VERIFY_WF, "story"), {"PR_TITLE": title, "PR_BODY": body})
        assert rc == (0 if want else 1) and (want is None or _gho(d) == want + "\n"), (title, out)


def test_azure_verify_template_reads_the_id_with_the_target_config_and_runs_the_exact_command(tmp_path, shell_flags):
    rc, out = _run_az_verify(tmp_path, _v_ado(tmp_path, verify_doc={"ok": True, "findings": []}), branch="refs/heads/feature/ab1001-x")
    assert rc == 0, out
    assert _calls(tmp_path)[:2] == [CALL_MODE, "branch --id-from=feature/ab1001-x --base origin/main | token=unset gh=unset"]
    assert _calls(tmp_path)[2] == CALL_VERIFY


def test_verify_job_scratch_files_are_stale_proof(tmp_path, shell_flags):
    (tmp_path / "rt").mkdir()
    (tmp_path / "rt" / "verify.json").write_text(json.dumps({"findings": [{"rule": "STALE"}]}))  # a stale file is never this run's findings
    rc, out = _gh_run(tmp_path, _gh(VERIFY_WF, "verify"), VERIFY_STEP_ENV, ado=LOG + "exit 2\n")
    assert rc == 1 and "STALE" not in out and "none" in out, out


# ---- V3-14b fix round 1: verify the PR HEAD (as the gate's --head), not the pull_request merge commit ----
def _git_log(tmp_path):
    p = tmp_path / "git.log"
    return p.read_text().splitlines() if p.exists() else []


def test_azure_verify_checks_out_the_pr_head_before_any_ado_call(tmp_path, shell_flags):
    rc, out = _run_az_verify(tmp_path, _v_ado(tmp_path, verify_doc={"ok": True, "findings": []}))
    assert rc == 0, out
    assert _git_log(tmp_path) == [f"checkout --quiet --detach {SHA}"] and _calls(tmp_path)[0] == CALL_MODE


@pytest.mark.parametrize("sha", ["", "abc", "0123456789ABCDEF0123456789ABCDEF01234567", "g" * 40, "0" * 41, "--orphan", SHA + "\n" + SHA])
def test_azure_verify_refuses_a_head_sha_that_is_not_40_hex_before_git_or_ado_run(tmp_path, sha, shell_flags):
    rc, out = _run_az_verify(tmp_path, _v_ado(tmp_path), head_sha=sha)
    assert rc == 1 and (sha == "" or "type=error" in out), out  # an empty value trips the `:?` guard instead
    assert _git_log(tmp_path) == [] and _calls(tmp_path) == []


def test_azure_verify_fails_closed_when_the_checkout_of_the_head_fails(tmp_path, shell_flags):
    rc, out = _run_az_verify(tmp_path, _v_ado(tmp_path), git_rc=128)
    assert rc == 1 and "could not check out the PR head" in out and "type=error" in out, out
    assert _calls(tmp_path) == []  # no ado call, so no project code, on the wrong commit


def test_azure_verify_drops_a_token_from_the_verify_call_only_as_the_gate_does(tmp_path, shell_flags):
    rc, out = _run_az_verify(tmp_path, _v_ado(tmp_path, verify_doc={"ok": True, "findings": []}), extra_env={"ADO_TOKEN": "tok-secret"})
    assert rc == 0 and _calls(tmp_path)[-1] == CALL_VERIFY and "tok-secret" not in out, out  # `env -u ADO_TOKEN`: the call sees none


# ---- P3-13d: under a strict target a gate that cannot run fails the job (rule_drift is checked only by the gate) ----
GATE_RC_CASES = [  # gate exit code, gate.json blocking flag, expected job result under a strict target / an advisory target
    (0, False, 0, 0), (2, False, 1, 0), (1, False, 1, 0), (1, True, 1, 1), (3, False, 3, 3),
]
NOT_CHECKED = "rule drift was not checked"


@pytest.mark.parametrize("gate_rc,blocking,strict_rc,advisory_rc", GATE_RC_CASES)
def test_github_post_step_strict_target_fails_when_the_gate_cannot_run(tmp_path, gate_rc, blocking, strict_rc, advisory_rc, shell_flags):
    for mode, want in (("strict", strict_rc), ("advisory", advisory_rc)):
        d = tmp_path / mode
        (d / "rt").mkdir(parents=True)
        (d / "rt" / "gate.md").write_text("GATE-BODY\n")
        (d / "rt" / "gate.code").write_text(f"{gate_rc}\n")
        (d / "rt" / "gate.json").write_text(json.dumps({"blocking": blocking}))
        rc, out = _gh_run(d, _gh("ado-gate", STEP_POST), {"GH_TOKEN": "t", "PR_NUM": "7", "MODE": mode}, gh="exit 0\n")
        assert rc == want, (mode, out)
        if mode == "advisory" and gate_rc in (1, 2) and not blocking:
            assert "::warning::spec-kit-ado gate could not run" in out and "::error::" not in out, out  # advisory: today's warning only
        # "could not run" text only when the gate did not run: never for a blocking verdict (exit 1 + blocking: true), nor under advisory
        assert (NOT_CHECKED in out) == (mode == "strict" and gate_rc == 2 or mode == "strict" and gate_rc == 1 and not blocking), (mode, gate_rc, blocking, out)


def test_github_post_step_strict_target_missing_gate_verdict_fails(tmp_path, shell_flags):
    (tmp_path / "rt").mkdir()
    (tmp_path / "rt" / "gate.md").write_text("x\n")  # no gate.code: the gate step did not finish
    rc, out = _gh_run(tmp_path, _gh("ado-gate", STEP_POST), {"GH_TOKEN": "t", "PR_NUM": "7", "MODE": "strict"}, gh="exit 0\n")
    assert rc == 1 and "::error::" in out, out


# ---- P4-11: the close step reads `approvers` from the PRE-merge target (never the merged commit's own config) and logs the cross-check by rule id only ----
XC_BASE = "0123456789abcdef0123456789abcdef01234567"
XC_DOC = {"phase": "done", "mode": "strict", "ok": False,
          "findings": [{"rule": "decisions-xcheck-verdict", "detail": "HOSTILE-DETAIL ::error::x", "fix": "f"}, {"rule": "decisions-xcheck-marker", "detail": "d", "fix": "f"}]}


def test_github_link_on_merge_passes_the_pre_merge_base_sha_through_env_to_both_done_calls():
    step = next(s for s in steps(load("ado-link-on-merge")) if "ado done" in s.get("run", ""))
    assert step["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    calls = re.findall(r"ado \"done\" \"\$id\"[^\n]*", step["run"])
    assert len(calls) == 2 and all(' --base "$BASE_SHA" ' in c for c in calls), calls
    assert "${{" not in step["run"] and "merge_commit_sha" not in step["run"]  # the base is never derived from the merged commit


def test_azure_link_on_merge_uses_the_first_parent_of_the_merged_commit_as_base():
    script = next(s["script"] for s in yaml.safe_load(AZ_LINK.read_text())["steps"] if "ado done" in s.get("script", ""))
    assert "base_sha=$(git rev-parse --verify --quiet 'HEAD^1^{commit}' 2>/dev/null || true)" in script
    calls = re.findall(r"ado \"done\" \"\$id\"[^\n]*", script)
    assert len(calls) == 2 and all(' --base "$base_sha" ' in c for c in calls), calls


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_calls_done_with_the_base(tmp_path, path, monkeypatch):
    _link_with_ledger(tmp_path)
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(k, "t")
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, "t@example.invalid")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "base"], cwd=tmp_path, check=True)
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "merged AB#7"], cwd=tmp_path, check=True)
    snippet = (f"export IDS=7 CLOSE_STORY=true STATE= PR_NUM=1 PR_URL=u BRANCH=main SOURCE_VERSION=abcdef123456 BASE_SHA={XC_BASE}\n" + _link_snippet(path))
    rc, out = _run_snippet(tmp_path, snippet, "main", ado_body=_merge7_ado(READY, CLOSED))
    assert rc == 0, out
    done_calls = [c for c in _calls(tmp_path) if c.startswith("done 7")]
    expected = XC_BASE if path.parent.name != "pipelines" else first
    assert [c.rsplit(" ", 2)[-2:] for c in done_calls] == [["--base", expected]] * 2, done_calls


@pytest.mark.parametrize("path", LINK_FILES, ids=["github", "azure"])
def test_link_on_merge_logs_the_cross_check_by_rule_id_only(tmp_path, path, shell_flags):
    family = "azure" if path.parent.name == "pipelines" else "github"
    rc, out = _link_run(tmp_path, path, _refused_ado(tmp_path, doc=XC_DOC), ids="7")
    assert rc == 1 and "7: strict done refused: decisions-xcheck-verdict decisions-xcheck-marker" in out and "HOSTILE-DETAIL" not in out, out
    # advisory: the dry run succeeds with warnings; only the matching rule ids are logged, whatever else a warning holds
    doc = {"ready": True, "warnings": ["decisions-xcheck-verdict: HOSTILE ::error::pwn ##vso[task.complete result=Succeeded]", "decisions-xcheck-dropped: x", "other note"]}
    ado = _merge7_ado(json.dumps(doc), CLOSED)
    rc, out = _link_run(tmp_path, path, ado, ids="7")
    assert rc == 0, out
    (line,) = [l for l in out.splitlines() if "done cross-check" in l]
    prefix = "::warning::" if family == "github" else "##vso[task.logissue type=warning]"
    assert line == f"{prefix}7: done cross-check: decisions-xcheck-dropped decisions-xcheck-verdict", out
    assert "HOSTILE" not in out and "DoD complete" in out  # advisory: the story still closes
    # no warnings: no line
    rc, out = _link_run(tmp_path, path, _merge7_ado(READY, CLOSED), ids="7")
    assert rc == 0 and "cross-check" not in out


# ---- P4-11 fix round: Azure HEAD^1 is the pre-merge target only for a merge commit or a squash; a one-parent HEAD with changed trust keys is refused ----
TGT_CFG = '{"organization": "contoso", "project": "Fabrikam", "approvers": ["55555555-5555-4555-8555-555555555555"]}'
PR_CFG = '{"organization": "contoso", "project": "Fabrikam"}'  # a PR that drops the approvers


def _topo(tmp_path, monkeypatch, kind, pr_changes_trust, older=0, tgt=TGT_CFG, pr=PR_CFG):
    """A repo whose last commit is the merge result of a 1- or 2-commit PR by `kind` (merge, squash, rebase-ff, rebase-1): the target had `approvers`;
    `older` further commits sit between the target's trust change and the merge."""
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
        monkeypatch.setenv(k, v)

    def git(*a):
        subprocess.run(["git", "-c", "commit.gpgsign=false", *a], cwd=tmp_path, check=True, capture_output=True)

    def write(name, text):
        (tmp_path / name).write_text(text)
        git("add", "-A", "--", name)
    git("init", "-q", "-b", "main")
    write("ado.config.json", tgt)
    git("commit", "-q", "-m", "main: approvers configured")
    for n in range(older):
        write("older", str(n))
        git("commit", "-q", "-m", f"older {n}")
    write("g", "main moves on")
    git("commit", "-q", "-m", "main moves on")
    git("checkout", "-q", "-b", "pr", "HEAD~1")
    if kind == "rebase-1":
        if pr_changes_trust:
            write("ado.config.json", pr)
        write("f", "all")
        git("commit", "-q", "-m", "AB#7 all in one")
    else:
        if pr_changes_trust:
            write("ado.config.json", pr)
        write("e", "first")
        git("commit", "-q", "-m", "first")
        write("f", "AB#7")
        git("commit", "-q", "-m", "AB#7 done")
    git("checkout", "-q", "main")
    if kind == "merge":
        git("merge", "-q", "--no-ff", "pr", "-m", "Merged PR 7: AB#7")
    elif kind == "squash":
        git("merge", "-q", "--squash", "pr")
        git("commit", "-q", "-m", "Merged PR 7: AB#7")
    else:
        git("checkout", "-q", "pr")
        git("rebase", "-q", "main")
        git("checkout", "-q", "main")
        git("merge", "-q", "--ff-only", "pr")


def _az_close(tmp_path, flags):
    (tmp_path / "dod").mkdir(exist_ok=True)
    (tmp_path / "dod" / "DoD_7.md").write_text("x")
    snippet = "export IDS=7 CLOSE_STORY=true STATE= PR_NUM=1 PR_URL=u BRANCH=main SOURCE_VERSION=abcdef123456\n" + _link_snippet(AZ_LINK)
    rc, out = _run_snippet(tmp_path, snippet, "main", ado_body=_merge7_ado(READY, CLOSED), flags=flags)
    return rc, out, _calls(tmp_path)


TOPOLOGIES = [  # kind, the PR changes the trust keys, refused
    ("merge", True, False), ("merge", False, False),
    ("squash", False, False), ("squash", True, True),
    ("rebase-ff", False, False), ("rebase-ff", True, True),
    ("rebase-1", False, False), ("rebase-1", True, True),
]


@pytest.mark.parametrize("kind,changes,refused", TOPOLOGIES, ids=[f"{k}-{'trust-change' if c else 'no-change'}" for k, c, _ in TOPOLOGIES])
def test_azure_link_on_merge_refuses_to_close_when_the_pre_merge_target_cannot_be_told(tmp_path, monkeypatch, shell_flags, kind, changes, refused):
    _topo(tmp_path, monkeypatch, kind, changes)
    parents = subprocess.run(["git", "rev-list", "--parents", "-n", "1", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.split()[1:]
    assert len(parents) == (2 if kind == "merge" else 1)
    rc, out, calls = _az_close(tmp_path, shell_flags)
    done = [c for c in calls if c.startswith("done 7")]
    if refused:
        assert rc == 1 and not done, (out, calls)
        assert "##vso[task.logissue type=error]7: close refused: approvers, approval, unattended, roles or mode changed in the last 50 commits" in out and "ado done 7 locally" in out
    else:
        assert rc == 0 and len(done) == 2 and "close refused" not in out, (out, calls)
        assert all(c.endswith(f"--base {parents[0]}") for c in done), (done, parents)  # the first parent: the real target tip for a merge commit and a squash


@pytest.mark.parametrize("older,refused", [(10, True), (60, False)])
def test_azure_trust_change_window_is_the_last_50_first_parent_commits_documented_residual(tmp_path, monkeypatch, older, refused):
    # PINS THE DOCUMENTED RESIDUAL (padding): the target's trust keys were last changed `older` commits before a squash that changed nothing itself; only a
    # change inside the 50-commit window refuses, so a PR that drops `approvers` and then adds 50 more commits gets past the guard (the merge-type limit is the control)
    _topo(tmp_path, monkeypatch, "squash", False, older=0)
    (tmp_path / "ado.config.json").write_text(PR_CFG)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-am", "approvers dropped"], cwd=tmp_path, check=True, capture_output=True)
    for n in range(older):
        (tmp_path / "pad").write_text(str(n))
        subprocess.run(["git", "add", "-A", "--", "pad"], cwd=tmp_path, check=True)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", f"pad {n}"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "f").write_text("AB#7 again")
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-am", "Merged PR 8: AB#7"], cwd=tmp_path, check=True, capture_output=True)
    rc, out, calls = _az_close(tmp_path, ["-e"])
    assert ("close refused" in out) == refused and (rc == 1) == refused, out
    assert bool([c for c in calls if c.startswith("done 7")]) != refused


def test_azure_one_parent_check_tolerates_an_invalid_or_missing_config(tmp_path, monkeypatch):
    _topo(tmp_path, monkeypatch, "squash", False)
    (tmp_path / "ado.config.json").write_text("{not json")  # a version that cannot be read differs from the others: refused
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-am", "Merged PR 9: AB#7"], cwd=tmp_path, check=True, capture_output=True)
    rc, out, calls = _az_close(tmp_path, ["-eo", "pipefail"])
    assert rc == 1 and "close refused" in out and not [c for c in calls if c.startswith("done 7")]


STRICT_CFG = '{"organization": "contoso", "project": "Fabrikam", "mode": "strict", "approvers": ["55555555-5555-4555-8555-555555555555"]}'
ADVISORY_CFG = '{"organization": "contoso", "project": "Fabrikam", "mode": "advisory", "approvers": ["55555555-5555-4555-8555-555555555555"]}'


@pytest.mark.parametrize("kind", ["rebase-ff", "rebase-1", "squash"])
def test_azure_a_pr_that_only_sets_advisory_mode_is_refused_too(tmp_path, monkeypatch, shell_flags, kind):
    # `mode` is read from the target as well: a PR that keeps `approvers` but sets `mode: advisory` would turn the blocking cross-check into a warning
    _topo(tmp_path, monkeypatch, kind, True, tgt=STRICT_CFG, pr=ADVISORY_CFG)
    rc, out, calls = _az_close(tmp_path, shell_flags)
    assert rc == 1 and "close refused: approvers, approval, unattended, roles or mode changed" in out and not [c for c in calls if c.startswith("done 7")], out
    _topo_dir = tmp_path / "ctl"
    _topo_dir.mkdir()
    _topo(_topo_dir, monkeypatch, kind, False, tgt=STRICT_CFG, pr=ADVISORY_CFG)  # the same repo without that change closes
    rc, out, calls = _az_close(_topo_dir, shell_flags)
    assert rc == 0 and len([c for c in calls if c.startswith("done 7")]) == 2, out


@pytest.mark.parametrize("kind,changes", [("squash", False), ("rebase-ff", False), ("merge", False)])
def test_azure_a_shallow_checkout_is_refused(tmp_path, monkeypatch, shell_flags, kind, changes):
    src = tmp_path / "src"
    src.mkdir()
    _topo(src, monkeypatch, kind, changes)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "2", f"file://{src}", str(clone)], check=True, capture_output=True)
    assert subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=clone, capture_output=True, text=True).stdout.strip() == "true"
    rc, out, calls = _az_close(clone, shell_flags)
    assert rc == 1 and "close refused" in out and "shallow" in out and not [c for c in calls if c.startswith("done 7")], out
    full = tmp_path / "full"
    subprocess.run(["git", "clone", "-q", f"file://{src}", str(full)], check=True, capture_output=True)
    rc, out, calls = _az_close(full, shell_flags)  # the same history in full closes
    assert rc == 0 and len([c for c in calls if c.startswith("done 7")]) == 2, out


def test_azure_an_unexpected_answer_of_the_history_check_is_refused(tmp_path, monkeypatch, shell_flags):
    # zero commits listed (or anything but exactly one distinct config) is not "unchanged": a git that answers nothing fails closed
    _topo(tmp_path, monkeypatch, "squash", False)
    stub = tmp_path / "git"
    stub.write_text('#!/bin/bash\ncase "$*" in "rev-list --first-parent"*) exit 0;; esac\nexec /usr/bin/git "$@"\n')
    stub.chmod(0o755)
    rc, out, calls = _az_close(tmp_path, shell_flags)
    assert rc == 1 and "close refused" in out and not [c for c in calls if c.startswith("done 7")], out


def test_speckit_contract_workflow_is_a_secret_free_weekly_canary():  # P4-15
    wf = load("speckit-contract")
    text = (WF_DIR / "speckit-contract.yml").read_text()
    t = triggers(wf)
    assert set(t) == {"schedule", "workflow_dispatch"} and re.fullmatch(r"\S+ \S+ \S+ \S+ \S+", t["schedule"][0]["cron"])
    assert wf["permissions"] == {"contents": "read"}
    assert "secrets." not in text and "github.token" not in text and "GH_TOKEN" not in text and "pull_request" not in text
    job = wf["jobs"]["contract"]
    rows = {r["speckit"]: r["ref"] for r in job["strategy"]["matrix"]["include"]}
    assert set(rows) == {"minimum", "latest"} and job["strategy"]["fail-fast"] is False
    assert rows["minimum"] == "9dca9bf42a031765ee5e5f2371137f2f26967e11"  # the commit ci.yml installs: v1.2.1, the supported floor
    assert rows["minimum"] in (WF_DIR / "ci.yml").read_text() and rows["latest"] == ""
    for step in steps(wf):  # every action pinned by commit sha, no credentials kept in the checkout
        if "uses" in step:
            assert re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    checkout = [s for s in steps(wf) if s.get("uses", "").startswith("actions/checkout@")]
    assert checkout and checkout[0]["with"]["persist-credentials"] is False
    # Spec Kit comes from an explicit sha or a validated release tag, never a branch; the matrix value reaches the shell through env only
    resolve = next(s for s in steps(wf) if s.get("name") == "Resolve the Spec Kit ref")
    assert resolve["env"] == {"REF": "${{ matrix.ref }}"} and "[0-9a-f]{40}" in resolve["run"]
    install = next(s for s in steps(wf) if s.get("name") == "Install Spec Kit CLI")
    assert "spec-kit.git@${SPECKIT_REF}" in install["run"] and "@main" not in text
    run = next(s for s in steps(wf) if s.get("name") == "Run the contract tests")
    assert run["env"] == {"SPECKIT_CONTRACT": "1"} and "tests/test_speckit_contract.py" in run["run"]
    assert "| tee" in run["run"] and run["shell"] == "bash"  # bash brings pipefail: without it a failing pytest would show green behind `| tee`
    assert any("skipped" in s.get("run", "") for s in steps(wf))  # an all-skipped run is a failure, not a pass


# ---- P4-18: the ci matrix ----
def test_ci_test_job_runs_on_python_3_9_and_3_13_with_the_pins_kept():
    wf = load("ci")
    job = wf["jobs"]["test"]
    assert job["strategy"]["matrix"] == {"python-version": ["3.9", "3.13"]} and job["strategy"]["fail-fast"] is False
    assert 'requires-python = ">=3.9"' in (WF_DIR.parent.parent / "pyproject.toml").read_text()  # the matrix floor is the declared floor
    setup = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python@"))
    assert re.fullmatch(r"actions/setup-python@[0-9a-f]{40}", setup["uses"])
    assert setup["uses"] == next(s for s in steps(load("speckit-contract")) if s.get("uses", "").startswith("actions/setup-python@"))["uses"]
    assert setup["with"] == {"python-version": "${{ matrix.python-version }}"}  # the matrix value is an expression in `with`, never in a `run`
    # Spec Kit needs Python 3.11 or later: installed on the 3.13 leg only, where CI=true makes a missing `specify` fail instead of skip
    install = next(s for s in job["steps"] if s.get("name") == "Install Spec Kit CLI (pinned)")
    assert install["if"] == "matrix.python-version != '3.9'" and "9dca9bf42a031765ee5e5f2371137f2f26967e11" in install["run"]
    runs = [s for s in job["steps"] if s.get("run") == "pytest -q"]
    base = {"ADO_REQUIRE_BASELINE": "1"}  # a missing baseline commit fails the byte-identity tests on both legs instead of skipping them
    assert [(s["if"], s.get("env")) for s in runs] == [("matrix.python-version != '3.9'", base), ("matrix.python-version == '3.9'", {"CI": "", **base})]
    # a hosted runner label with a Python 3.9 build, and a full-history, credential-free, sha-pinned checkout (the contract workflow's)
    assert job["runs-on"] == "ubuntu-24.04"
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"]) and checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
    assert checkout["uses"] == next(s for s in steps(load("speckit-contract")) if s.get("uses", "").startswith("actions/checkout@"))["uses"]
    # the rest of the workflow is as before: same jobs, read-only permissions, no secret in the matrix job
    assert sorted(wf["jobs"]) == ["actionlint", "scrub", "test"] and wf["permissions"] == {"contents": "read"}
    assert "secrets." not in json.dumps(job) and "permissions" not in job


CHECKOUT_SHA, SETUP_PYTHON_SHA = "11bd71901bbe5b1630ceea73d27597364c9af683", "a26af69be951a213d495a4c3e4e4022e16d87065"  # v4.2.2, v5.6.0 (git ls-remote, F7)


def test_every_third_party_action_is_pinned_by_commit_sha_in_every_workflow():
    """F7 (C M-1): the toolkit's own jobs and the five consumer templates (which hold ADO_TOKEN) use the same two commits, never a movable tag.
    Residual (not closed here): `ci.yml`'s `actionlint` job pipes the pinned tag's download script into bash with no checksum."""
    pinned = {"actions/checkout": CHECKOUT_SHA, "actions/setup-python": SETUP_PYTHON_SHA}
    seen = 0
    for name in ALL:
        for s in steps(load(name)):
            use = s.get("uses", "")
            if use.startswith("actions/"):
                seen += 1
                action, _, ref = use.partition("@")
                assert pinned[action] == ref, (name, use)
    assert seen >= 16
    # the toolkit's read-only checkouts that never push keep no credentials in .git/config
    for job in ("scrub", "actionlint"):
        co = next(s for s in load("ci")["jobs"][job]["steps"] if s.get("uses", "").startswith("actions/checkout@"))
        assert co["with"]["persist-credentials"] is False, job


# ---- CI-1: the inline scripts are shellchecked here too (the `actionlint` CI job, which runs shellcheck, stays the authority) ----
def _inline_scripts():
    """(label, bash text) of every `run:` step in the workflows and `script:` step in the Azure templates, expressions made inert."""
    for name in ALL:
        for step in steps(load(name)):
            if step.get("run") and step.get("shell", "bash") == "bash":
                yield f"{name} {step.get('name', '')}", step["run"]
    for p in sorted((ROOT / "pipelines").glob("*.yml")):
        for step in yaml.safe_load(p.read_text())["steps"]:
            if step.get("script"):
                yield f"{p.name} {step.get('displayName', '')}", step["script"]


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed (the actionlint CI job runs it)")
def test_inline_scripts_are_shellcheck_clean():
    bad = []
    for label, text in _inline_scripts():
        text = re.sub(r"\$\{\{.*?\}\}", "_expr_", text)  # GitHub / Azure template expressions
        text = re.sub(r"\$\((?:\w+\.)+\w+\)", "_macro_", text)  # Azure macros such as $(Agent.BuildDirectory), not command substitution
        r = subprocess.run(["shellcheck", "-s", "bash", "-f", "gcc", "-"], input=text, capture_output=True, text=True)
        bad += [f"{label}: {ln}" for ln in r.stdout.splitlines()]
    assert not bad, "\n".join(bad)
