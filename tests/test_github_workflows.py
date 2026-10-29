"""Static checks on .github/workflows (actionlint itself runs in CI)."""
import json
import re
from pathlib import Path

import pytest
import yaml

WF_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
REUSABLE = ["ado-link-on-merge", "ado-gate", "ado-branch-guard"]
ALL = REUSABLE + ["ci", "commit-lint"]
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


@pytest.mark.parametrize("name", ALL)
def test_every_workflow_has_least_privilege_permissions(name):
    perms = load(name).get("permissions")
    assert perms and perms.get("contents") == "read"
    extra = {k for k in perms if k != "contents"}
    assert extra == ({"pull-requests"} if name == "ado-gate" else set())


@pytest.mark.parametrize("name", REUSABLE)
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
    fail = [ln for ln in run.splitlines() if "exit 1" in ln]
    assert len(fail) == 1 and '"$code" -eq 1' in fail[0] and '["blocking"] is True' in fail[0]


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


@pytest.mark.parametrize("name", REUSABLE)
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
    assert len(CI_FILES) == 6
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


def _run_snippet(tmp_path, snippet, branch, *, get_code=0, config=True, have_ado=True, temp=None, ado_body=None):
    """Run a CI shell snippet; `ado branch` is the real CLI, `ado get` a stub. Returns (exit code, output)."""
    import os, subprocess, sys
    base_env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    probe = subprocess.run([sys.executable, "-c", ADO_CLI, "--help"], env=base_env, cwd=tmp_path, capture_output=True)
    assert probe.returncode == 0, probe.stderr  # this interpreter's own ado (never a PATH one); fail, don't skip
    if have_ado:  # `ado branch ...` -> this interpreter's CLI; `ado get` -> canned answer
        stub = tmp_path / "ado"
        stub.write_text("#!/bin/bash\n" + ado_body if ado_body else f'#!/bin/bash\n[ "$1" = branch ] && exec "{sys.executable}" -c \'{ADO_CLI}\' "$@"\n'
                        f'c={get_code}; [ $c = 0 ] && exit 0; echo "HTTP $c" >&2; exit 1\n')
        stub.chmod(0o755)
    cfg = tmp_path / "ado.config.json"
    if config:
        cfg.write_text(config if isinstance(config, str) else '{"organization": "contoso", "project": "Fabrikam"}')
    script = tmp_path / "snippet.sh"
    script.write_text(snippet)
    env = {**base_env, "PATH": (f"{tmp_path}:" if have_ado else "") + "/usr/bin:/bin", "RUNNER_TEMP": str(temp or tmp_path),
           "ADO_CONFIG": str(cfg), "BRANCH": branch, "SOURCE_BRANCH": branch}
    if temp is False:  # the runner did not set its temp dir
        env.pop("RUNNER_TEMP")
    r = subprocess.run(["bash", str(script)], env=env, cwd=tmp_path, capture_output=True, text=True)
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
    ("feature/ab12-x", {}, "ID=12"), ("001-x", {}, None), ("001-x", {"config": False}, None),
    ("feature/ab12-x", {"have_ado": False}, None)])
def test_azure_gate_id_step_skips_never_fails(tmp_path, branch, kw, id_line):
    rc, out = _run_snippet(tmp_path, _gate_id_snippet(), branch, **kw)
    if id_line is not None:
        assert rc == 0 and id_line in out and "skipped" not in out, out
    else:  # no id, no config or no ado: exit 0 (skipped) before the "ID=" echo
        assert rc == 0 and "skipped" in out and "ID=" not in out, out


# ---- F29: the gate step fails closed on exit 3 (invalid config), runs the real CLI ----
def _gate_step_run(path):
    if path.parent.name == "pipelines":
        run = next(s["script"] for s in yaml.safe_load(path.read_text())["steps"] if "ado gate" in s.get("script", ""))
        return run.replace("${AGENT_TEMPDIRECTORY", "${RUNNER_TEMP").replace("$AGENT_TEMPDIRECTORY", "$RUNNER_TEMP").replace("${TARGET_BRANCH#refs/heads/}", "main")
    run = gate_run()
    return run.replace('"origin/${BASE_REF}"', "origin/main").replace("origin/${BASE_REF}", "origin/main")


def _run_gate_step(tmp_path, path, config, *, block=(), temp=None, ado_body=None):
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
           "TARGET_BRANCH": "refs/heads/main"}
    env.pop("ADO_TOKEN", None)
    env["HOME"] = str(tmp_path)  # no real keychain/.azdo token
    if temp is False:  # the runner did not set its temp dir
        env.pop("RUNNER_TEMP")
    r = subprocess.run(["bash", str(script)], env=env, cwd=tmp_path, capture_output=True, text=True)
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
                                                    ROOT / "pipelines" / "ado-link-on-merge.yml"]),
    "github": ("::warning::", [WF_DIR / "ado-branch-guard.yml", WF_DIR / "commit-lint.yml", WF_DIR / "ado-gate.yml",
                               WF_DIR / "ado-link-on-merge.yml"]),
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
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip().startswith("ann() {")]
    assert len(lines) == 1, path
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
    annotation = re.compile(r"echo \"(##vso\[task\.logissue|::(error|warning|notice)::|ado gate: )")  # log lines that carry CLI stderr count too
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
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("commit", "-q", "--allow-empty", "-m", "not conventional")
    seen = _plant(tmp_path)
    run = next(s["run"] for s in steps(load("commit-lint")) if "ann()" in s.get("run", ""))
    r = subprocess.run(["bash", "-c", run], env={**os.environ, "BASE_REF": "main"}, cwd=tmp_path, capture_output=True, text=True)
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
    body = ('case "$1" in branch) echo 1001; exit 0;; esac\n' + STDOUT_ADO + STDERR_ADO + "exit 1\n")  # gate and pr comment both fail loudly
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
            f' "done 7 --apply") echo \'{apply_json}\'; exit {apply_rc};;\nesac\nexit 0\n')


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
    assert "done 7 --apply" in calls and not any(c.startswith("update ") for c in calls), calls


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
