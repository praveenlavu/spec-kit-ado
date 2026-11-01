"""Static checks on pipelines/ (Azure Pipelines step templates)."""
import re
from pathlib import Path

import pytest
import yaml

PL_DIR = Path(__file__).resolve().parent.parent / "pipelines"
NAMES = ["ado-link-on-merge", "ado-gate", "ado-branch-guard"]
SECRETFREE = ["ado-strict-verify"]  # V3-14b: no token of any kind
UNTRUSTED = re.compile(r"\$\((System\.PullRequest\.|Build\.SourceBranch|Build\.SourceVersionMessage)")


def load(name):
    return yaml.safe_load((PL_DIR / f"{name}.yml").read_text())


def scripts(name):
    return [s["script"] for s in load(name)["steps"] if "script" in s]


@pytest.mark.parametrize("name", NAMES + SECRETFREE)
def test_templates_have_parameters_and_steps(name):
    t = load(name)
    assert isinstance(t["parameters"], list) and t["parameters"]
    assert isinstance(t["steps"], list) and t["steps"]
    text = (PL_DIR / f"{name}.yml").read_text()
    assert "pip install --quiet $(Agent.BuildDirectory)/spec-kit-ado-toolkit" in text
    assert "UsePythonVersion@0" in text and "'3.11'" in text


@pytest.mark.parametrize("name", NAMES)
def test_token_from_system_access_token(name):
    envs = [s["env"] for s in load(name)["steps"] if "env" in s and "ADO_TOKEN" in s["env"]]
    assert envs and all(e["ADO_TOKEN"] == "$(System.AccessToken)" for e in envs)
    for s in scripts(name):
        assert "set -x" not in s and "echo \"$ADO_TOKEN" not in s


@pytest.mark.parametrize("name", NAMES + SECRETFREE)
def test_no_untrusted_macros_in_script(name):
    for s in scripts(name):
        assert not UNTRUSTED.search(s), f"{name}: untrusted macro in script"
        assert "${{" not in s, f"{name}: template expression in script"


@pytest.mark.parametrize("name", NAMES + SECRETFREE)
def test_persist_credentials_false(name):
    checkouts = [s for s in load(name)["steps"] if "checkout" in s]
    assert {c["checkout"] for c in checkouts} >= {"self", "spec-kit-ado"}
    assert all(c.get("persistCredentials") is False for c in checkouts)


def _without_invalid_config_exit(script):
    """Drop the separately tested fail-closed blocks (the invalid-config exit, the strict-mode failures: each a top-level `if ...; fi`
    whose message starts `ADO gate FAILED` and ends in its own `exit 1`); everything else stays checked."""
    rest, n = re.subn(r'^if [^\n]*; then(?:(?!; fi\n)[^\n])*\n(?:(?!fi\n).)*?ADO gate FAILED.*?\nfi\n', "", script, flags=re.S | re.M)
    rest0, n0 = re.subn(r'^if \[ "\$code" -eq 3 \]; then.*?\nfi\n', "", script, flags=re.S | re.M)
    assert n0 == 1 and "ADO gate FAILED: ado.config.json is invalid" in script
    assert n == 7, n  # invalid config (strict mode, story id), mode failed, unreadable mode, strict needs an id, strict check failed, strict gate cannot run
    return rest


def test_gate_fails_only_on_blocking():
    (script,) = [s for s in scripts("ado-gate") if "ado gate" in s]
    assert "--changed" in script and "mapfile -d ''" in script
    assert '--base "origin/${TARGET_BRANCH#refs/heads/}"' in script  # env value, quoted
    assert "ado pr comment" in script and "--body-file" in script
    exits = [ln for ln in _without_invalid_config_exit(script).splitlines() if re.search(r"\bexit 1\b", ln)]
    assert exits and all("blocking" in ln for ln in exits)


def test_gate_comment_comes_from_cli_summary():
    (script,) = [s for s in scripts("ado-gate") if "ado gate" in s]
    assert "--summary-out" in script and '> "$AGENT_TEMPDIRECTORY/gate.json"' in script
    assert "ADO_TOKEN" not in script.replace("env -u ADO_TOKEN ado strict check ", "") and "${msg" not in script and "comment.md" not in script
    assert 'ado pr comment "$PR_ID" --body-file "$AGENT_TEMPDIRECTORY/gate.md"' in script
    assert "could not run (see job log)" in script
    (fail,) = [ln for ln in _without_invalid_config_exit(script).splitlines() if "exit 1" in ln]
    assert '"$code" -eq 1' in fail and '["blocking"] is True' in fail


def test_link_on_merge_dry_run_before_apply():
    (script,) = [s for s in scripts("ado-link-on-merge") if "ado done" in s]
    assert script.index("--plan-out") < script.index("--apply")
    assert "task.logissue type=warning" in script


@pytest.mark.parametrize("name", NAMES + SECRETFREE)
def test_checkouts_have_explicit_paths(name):
    by = {c["checkout"]: c for c in load(name)["steps"] if "checkout" in c}
    assert by["spec-kit-ado"]["path"] == "spec-kit-ado-toolkit"
    assert by["self"]["path"] == "${{ parameters.repoPath }}"
    params = {p["name"]: p for p in load(name)["parameters"]}
    assert params["repoPath"]["default"]


def test_branch_guard_not_found_keys_on_404():
    (script,) = scripts("ado-branch-guard")[-1:]
    assert "HTTP 404" in script
    # a non-404 error is a warning + success, never a failure
    assert "could not be verified" in script
    exits = [ln for ln in script.splitlines() if re.search(r"\bexit 1\b", ln)]
    assert len(exits) == 2  # no story id, or the story is positively not found
    assert 'ado branch --id-from="$branch"' in script and "grep -oE '[0-9]+'" not in script


def test_link_on_merge_closes_story_only_when_opted_in():
    tpl = load("ado-link-on-merge")
    (param,) = [p for p in tpl["parameters"] if p["name"] == "closeStory"]
    assert param["type"] == "boolean" and param["default"] is False
    (step,) = [s for s in tpl["steps"] if "ado done" in s.get("script", "")]
    assert step["env"]["CLOSE_STORY"] == "${{ parameters.closeStory }}"
    script = step["script"]
    # `ado done` lives in close_out(), which the opt-in guard alone calls
    assert script.count("close_out") == 2 and 'case "$CLOSE_STORY" in [Tt]rue) close_out && closed=true ;; esac' in script


def test_gate_removes_stale_summary_before_running():
    (script,) = [s for s in scripts("ado-gate") if "ado gate" in s]
    assert script.index('rm -f "$AGENT_TEMPDIRECTORY/gate.md"') < script.index("ado gate ")


WARN_AZ = 'echo "##vso[task.logissue type=warning]spec-kit-ado gate could not run (see the PR comment and job log)"'


def test_gate_warns_when_gate_could_not_run():
    (script,) = [s for s in scripts("ado-gate") if "ado gate" in s]
    assert script.count(WARN_AZ) == 1
    (line,) = [ln for ln in script.splitlines() if WARN_AZ in ln]
    assert '"$code" -eq 0' in line and '"$code" -eq 1' in line and "blocking" in line


def test_gate_failed_diff_counts_as_could_not_run():
    (script,) = [s for s in scripts("ado-gate") if "ado gate" in s]
    assert script.index("else code=2; fi") < script.index(WARN_AZ)


def test_gate_reads_story_with_cli_rule():
    script = scripts("ado-gate")[-1]
    assert 'ado branch --id-from="$branch"' in script and "grep -oE '[0-9]+'" not in script
    assert load("ado-gate")["steps"][-1]["env"]["SOURCE_BRANCH"] == "$(System.PullRequest.SourceBranch)"


# ---- V3-14b: the optional secret-free re-run job (shipped unrun: hosted agents are billing-blocked) ----
def test_strict_verify_template_has_no_token_and_never_persists_credentials():
    text = (PL_DIR / "ado-strict-verify.yml").read_text()
    text = text.replace("env -u ADO_TOKEN ado strict verify ", "")  # the one allowed mention: the step drops a token it never maps, as the gate does
    for word in ("ADO_TOKEN", "System.AccessToken", "SYSTEM_ACCESSTOKEN", "GH_TOKEN", "GITHUB_TOKEN", "pull_request_target", "secrets."):
        assert word not in text, word  # the whole file, comments included
    t = load("ado-strict-verify")
    assert all("env" not in s or not {"ADO_TOKEN", "SYSTEM_ACCESSTOKEN"} & set(s["env"]) for s in t["steps"])
    own, toolkit = [s for s in t["steps"] if "checkout" in s]
    assert own["checkout"] == "self" and own["fetchDepth"] == 0 and own["persistCredentials"] is False
    assert toolkit["checkout"] == "spec-kit-ado" and toolkit["persistCredentials"] is False
    assert all(s["condition"] == "eq(variables['Build.Reason'], 'PullRequest')" for s in t["steps"])  # every step, PR validation only


def test_strict_verify_template_runs_the_exact_command_after_the_mode_and_the_id():
    (script,) = [s for s in scripts("ado-strict-verify") if "strict verify" in s]
    mode = 'ado strict mode --base "origin/${TARGET_BRANCH#refs/heads/}" > "$AGENT_TEMPDIRECTORY/mode.json"'
    ident = 'ado branch --id-from="$branch" --base "origin/${TARGET_BRANCH#refs/heads/}"'
    verify = 'env -u ADO_TOKEN ado strict verify "$id" --no-record --base "origin/${TARGET_BRANCH#refs/heads/}" $allow > "$AGENT_TEMPDIRECTORY/verify.json"'
    checkout = 'git checkout --quiet --detach "$HEAD_SHA"'
    assert script.index(checkout) < script.index(mode)  # the PR head first, before any ado call (as the gate's --head)
    assert script.index(mode) < script.index(ident) < script.index(verify) and len(re.findall(r"(?m)^\s*(?:env -u ADO_TOKEN )?ado strict verify ", script)) == 1
    assert "--record" not in script.replace("--no-record", "") and "ado gate" not in script and "ado pr comment" not in script
    assert script.splitlines()[0] == ': "${AGENT_TEMPDIRECTORY:?AGENT_TEMPDIRECTORY is not set - refusing to write scratch files into the checkout}"'
    for var in ("SOURCE_BRANCH", "TARGET_BRANCH"):
        assert f': "${{{var}:?' in script
    (step,) = [s for s in load("ado-strict-verify")["steps"] if "strict verify" in s.get("script", "")]
    assert step["env"] == {"SOURCE_BRANCH": "$(System.PullRequest.SourceBranch)", "TARGET_BRANCH": "$(System.PullRequest.TargetBranch)",
                           "HEAD_SHA": "$(System.PullRequest.SourceCommitId)",
                           "ALLOW_OVERRIDE": "${{ parameters.allowOverride }}"}
    assert ': "${HEAD_SHA:?' in script
    assert [p["name"] for p in load("ado-strict-verify")["parameters"]] == ["repoPath", "allowOverride"]
    (allow,) = [p for p in load("ado-strict-verify")["parameters"] if p["name"] == "allowOverride"]
    assert allow["type"] == "boolean" and allow["default"] is False  # an override fails the re-run unless the caller accepts it
    assert 'case "$ALLOW_OVERRIDE" in [Tt]rue) allow=--allow-override ;; esac' in script


@pytest.mark.parametrize("gate_rc,blocking,strict_rc,advisory_rc", [(0, False, 0, 0), (2, False, 1, 0), (1, False, 1, 0), (3, False, 1, 0), (1, True, 1, 1)])
def test_gate_strict_target_fails_when_the_gate_cannot_run_but_advisory_only_warns(tmp_path, gate_rc, blocking, strict_rc, advisory_rc):
    from tests import test_github_workflows as t  # real bash, an `ado` shim
    for mode, want in (("strict", strict_rc), ("advisory", advisory_rc)):
        d = tmp_path / mode
        d.mkdir()
        rc, out = t._run_az(d, mode=(mode, 0), gate_rc=gate_rc, blocking=blocking, strict_rc=0, strict_doc={"ok": True, "findings": []})
        assert rc == want, (mode, gate_rc, out)
        if mode == "advisory" and gate_rc and not blocking:
            assert "##vso[task.logissue type=warning]spec-kit-ado gate could not run" in out and "type=error" not in out, out
        could_not_run = mode == "strict" and gate_rc != 0 and not blocking
        assert ("rule drift was not checked" in out) == could_not_run, (mode, gate_rc, blocking, out)  # never said of a blocking verdict
        if could_not_run:
            assert "##vso[task.logissue type=error]ADO gate FAILED:" in out
