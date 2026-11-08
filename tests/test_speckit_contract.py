"""Contract tests against the REAL `specify` CLI (never imported: it has its own interpreter).

They pin what spec-kit-ado relies on in Spec Kit's workflow engine (design findings R:28-36): a gate pauses without a TTY, `verdict_input` plus
`resume --input` completes it, a resume restarts at the current nested step, a verdict persists into a later loop iteration until a resume resets it,
the `state.json` keys `ado status` reads, `execution.version`, and that the shipped workflow validates. Skipped unless `specify` is on PATH and
SPECKIT_CONTRACT=1 (CI: .github/workflows/speckit-contract.yml, weekly and on demand). Each test runs `specify` in its own temp project with a private
HOME, no TTY and no network; no step calls `ado`. See docs/compatibility.md for the versions tried.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ado import approvals, status

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "contract-workflow.yml"
SHIPPED = ROOT / "workflows" / "ado-lifecycle" / "workflow.yml"
SPECIFY = shutil.which("specify")

pytestmark = pytest.mark.skipif(not SPECIFY or os.environ.get("SPECKIT_CONTRACT") != "1",
                                reason="needs the specify CLI on PATH and SPECKIT_CONTRACT=1")

STORY = 1001
GATE, NESTED = "review-spec", "approve-questions"  # a top-level gate and a gate inside the do-while `questions-loop`


class Project:
    """A temp Spec Kit project directory; every call is `specify ...` with no TTY (stdin is /dev/null)."""

    def __init__(self, tmp_path):
        self.dir = tmp_path / "proj"
        self.dir.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        self.env = {**os.environ, "HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"), "XDG_CONFIG_HOME": str(home / ".config"),
                    "XDG_DATA_HOME": str(home / ".local" / "share"), "NO_COLOR": "1", "TERM": "dumb"}

    def sp(self, *args):
        return subprocess.run([SPECIFY, *args], cwd=self.dir, env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)

    def start(self):
        cp = self.sp("workflow", "run", str(FIXTURE), "--input", f"story_id={STORY}", "--json")
        return cp, json.loads(cp.stdout)

    def resume(self, run_id, gate, verdict):
        """The resume exactly as the poller builds it (`approvals.resume_argv`): the real form is what the contract is checked against."""
        argv = approvals.resume_argv(run_id, gate, verdict)
        assert argv[:4] == ["specify", "workflow", "resume", run_id] and argv.count("--input") == 12 == len(argv[4:]) // 2
        return self.sp(*argv[1:])

    def state(self, run_id):
        return json.loads((self.dir / ".specify" / "workflows" / "runs" / run_id / "state.json").read_text(encoding="utf-8"))

    def trace(self):
        path = self.dir / "trace.txt"
        return path.read_text(encoding="utf-8").split() if path.exists() else []

    def mark(self, name, on=True):
        (self.dir / name).touch() if on else (self.dir / name).unlink()


@pytest.fixture
def proj(tmp_path):
    return Project(tmp_path)


def _paused_at_nested_gate(proj):
    cp, out = proj.start()
    assert proj.resume(out["run_id"], GATE, "approve").returncode == 0
    return out["run_id"]


def test_a_gate_without_a_tty_pauses_with_exit_0(proj):  # R:28
    cp, out = proj.start()
    assert cp.returncode == 0
    assert (out["status"], out["current_step_id"], out["gate"]["step_id"], out["gate"]["options"]) == ("paused", GATE, GATE, ["approve", "reject"])
    st = proj.state(out["run_id"])
    assert (st["status"], st["current_step_id"], st["step_results"][GATE]["status"]) == ("paused", GATE, "paused")
    assert proj.trace() == ["pull"]


def test_verdict_input_with_resume_completes_the_gate(proj):  # R:28
    _, out = proj.start()
    rid = out["run_id"]
    cp = proj.resume(rid, GATE, "approve")
    st = proj.state(rid)
    assert cp.returncode == 0 and st["status"] == "paused"
    assert st["step_results"][GATE]["status"] == "completed" and st["step_results"][GATE]["output"]["choice"] == "approve"
    assert st["current_step_id"].split(":")[-2 if ":" in st["current_step_id"] else 0] == NESTED  # now at the gate in the loop
    assert proj.trace() == ["pull", "preview"]  # `pull` did not run again
    assert st["inputs"]["verdict_review_spec"] == "approve" and st["inputs"]["verdict_approve_questions"] == ""
    assert proj.resume(rid, NESTED, "approve").returncode == 0
    st = proj.state(rid)
    assert st["status"] == "completed" and proj.trace() == ["pull", "preview", "apply", "applied"]


def test_a_reject_verdict_ends_the_run_without_running_later_steps(proj):  # R:28 (on_reject: abort)
    _, out = proj.start()
    cp = proj.resume(out["run_id"], GATE, "reject")
    st = proj.state(out["run_id"])
    assert cp.returncode != 0 and st["status"] in ("aborted", "failed")
    assert proj.trace() == ["pull"]


def test_resume_restarts_at_the_current_nested_step(proj):  # R:33
    rid = _paused_at_nested_gate(proj)
    proj.mark("fail-apply")
    cp = proj.resume(rid, NESTED, "approve")
    st = proj.state(rid)
    assert cp.returncode == 1 and st["status"] == "failed"
    assert st["current_step_id"].split(":")[-2 if ":" in st["current_step_id"] else 0] == "questions-apply"
    proj.mark("fail-apply", on=False)
    cp = proj.sp("workflow", "resume", rid)  # a plain resume: the failed nested step again, not the loop head and not the gate
    assert cp.returncode == 0 and proj.state(rid)["status"] == "completed"
    assert proj.trace() == ["pull", "preview", "apply", "apply", "applied"]  # one `preview`: the loop head did not run again


def test_a_persisted_verdict_passes_a_later_iteration_and_a_reset_stops_it(proj):  # R:34
    rid = _paused_at_nested_gate(proj)
    proj.mark("again")
    assert proj.resume(rid, NESTED, "approve").returncode == 0
    st = proj.state(rid)
    # iteration 2: the gate was passed by the verdict left in the inputs, with no prompt and nothing supplied (the hazard approvals.resume_argv guards)
    assert st["status"] == "completed" and st["inputs"]["verdict_approve_questions"] == "approve"
    assert proj.trace() == ["pull", "preview", "apply", "preview", "apply", "applied"]


def test_an_empty_input_resets_a_persisted_verdict(proj):  # R:34
    rid = _paused_at_nested_gate(proj)
    proj.mark("fail-apply")
    assert proj.resume(rid, NESTED, "approve").returncode == 1  # the verdict is now persisted; the run failed after the gate
    assert proj.state(rid)["inputs"]["verdict_approve_questions"] == "approve"
    proj.mark("fail-apply", on=False)
    proj.mark("again")
    # the resume of ANOTHER gate resets this one (`--input verdict_approve_questions=`): iteration 2 pauses at its gate instead of passing it
    cp = proj.resume(rid, GATE, "approve")
    st = proj.state(rid)
    assert cp.returncode == 0 and st["status"] == "paused" and st["inputs"]["verdict_approve_questions"] == ""
    assert st["current_step_id"].endswith(":1") and st["current_step_id"].split(":")[-2] == NESTED  # a later iteration: `<loop>:<step>:<n>` (R:33)
    assert proj.trace() == ["pull", "preview", "apply", "apply", "preview"]


def test_a_saved_verdict_survives_a_plain_resume(proj):  # R:34
    rid = _paused_at_nested_gate(proj)
    proj.mark("fail-apply")
    assert proj.resume(rid, NESTED, "approve").returncode == 1
    proj.mark("fail-apply", on=False)
    proj.mark("again")
    cp = proj.sp("workflow", "resume", rid)  # no --input at all: the saved verdict still passes the gate of iteration 2
    st = proj.state(rid)
    assert cp.returncode == 0 and st["status"] == "completed" and st["inputs"]["verdict_approve_questions"] == "approve"
    assert proj.trace() == ["pull", "preview", "apply", "apply", "preview", "apply", "applied"]


def test_state_json_has_the_keys_status_reads(proj):  # R:29, R:33
    _, out = proj.start()
    rid = out["run_id"]
    st = proj.state(rid)
    assert {"run_id", "workflow_id", "status", "current_step_id", "updated_at", "inputs", "step_results", "execution"} <= set(st)
    assert (st["run_id"], st["workflow_id"], st["status"]) == (rid, "ado-lifecycle", "paused")
    assert type(st["inputs"]["story_id"]) is int and st["inputs"]["story_id"] == STORY
    assert isinstance(st["updated_at"], str) and isinstance(st["step_results"], dict)
    got = status.read_run(str(proj.dir), STORY)  # the kit's own reader accepts a real file
    assert got and (got["run_id"], got["status"], got["step"]) == (rid, "paused", GATE)
    polled = approvals.read_run(str(proj.dir), rid)  # the poller's reader accepts the real file too (it raises ValueError on anything it cannot trust)
    assert (polled["run_id"], polled["status"], polled["step"], polled["story_id"]) == (rid, "paused", GATE, STORY)
    assert proj.resume(rid, GATE, "approve").returncode == 0  # same run, now at the gate inside the loop
    step = proj.state(rid)["current_step_id"]
    assert step.split(":")[-2 if ":" in step else 0] == NESTED
    assert status.read_run(str(proj.dir), STORY)["step"] == step == approvals.read_run(str(proj.dir), rid)["step"]
    proj.mark("again")
    proj.resume(rid, NESTED, "approve")  # leaves a later iteration behind; the ids stay shipped-id based
    assert all(sid.split(":")[-2 if ":" in sid else 0] in status.SHIPPED_STEPS for sid in proj.state(rid)["step_results"])


def test_execution_version_is_2(proj):  # R:29
    _, out = proj.start()
    execution = proj.state(out["run_id"])["execution"]
    assert isinstance(execution, dict) and execution["version"] == 2


def test_the_shipped_workflow_installs_and_info_lists_it(proj):  # P4-14 wiring (no R finding: checked by hand there)
    (proj.dir / ".specify").mkdir()
    cp = proj.sp("workflow", "add", str(SHIPPED))  # validates before installing (exit 1 and no install on an invalid workflow)
    assert cp.returncode == 0, cp.stderr
    cp = proj.sp("workflow", "info", "ado-lifecycle")
    shipped = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    assert cp.returncode == 0 and "ado-lifecycle" in cp.stdout
    for name in shipped["inputs"]:
        assert name in cp.stdout
    assert f"Steps ({len(shipped['steps'])})" in cp.stdout
    assert all(approvals._input(g) in shipped["inputs"] for g in approvals.GATES)  # the inputs the poller resumes with exist in the shipped workflow


def test_an_invalid_verdict_input_is_refused(proj):  # the validator really runs: a gate naming an undeclared input must not install
    (proj.dir / ".specify").mkdir()
    bad = proj.dir / "bad.yml"
    bad.write_text(FIXTURE.read_text(encoding="utf-8").replace("verdict_input: verdict_review_spec", "verdict_input: undeclared"), encoding="utf-8")
    cp = proj.sp("workflow", "add", str(bad))
    assert cp.returncode == 1 and "undeclared" in cp.stdout + cp.stderr


# ---- v0.3.1: a supplied verdict must not approve a plan that changed after the human saw it (Copilot review of github/spec-kit PR 5312) ----
LOOPS = ["questions", "plan-questions", "answers-questions", "push", "decisions", "sync", "pr"]
STUB_ADO = """#!/bin/sh
# Stand-in for `ado` (contoso, story 1001): --plan-out writes a plan, --expect-plan applies it. With the file `changes` present the FIRST apply finds
# the world changed since the preview (exit 2 + <file>.new, as the real command does); later previews write the new plan.
plan=""; expect=""
while [ $# -gt 0 ]; do case "$1" in --plan-out) plan="$2"; shift;; --expect-plan) expect="$2"; shift;; esac; shift; done
if [ -n "$plan" ]; then
  mkdir -p "$(dirname "$plan")"; rm -f "$plan.new"; echo preview >> trace.txt
  n=$(cat .calls 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > .calls
  if [ -e changes ] && [ "$n" -gt 1 ]; then echo "plan B" > "$plan"; else echo "plan A" > "$plan"; fi
  exit 0
fi
if [ -n "$expect" ]; then
  echo apply >> trace.txt
  if [ -e changes ] && [ ! -e .refused ]; then touch .refused; echo "plan B" > "$expect.new"; echo "plan changed since approval" >&2; exit 2; fi
  exit 0
fi
exit 0
"""


def _mini(proj, loop):
    """The SHIPPED `<loop>-loop` and `<loop>-applied` steps with the shipped inputs, run by the real engine against the stub `ado`."""
    wf = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    steps = {s["id"]: s for s in wf["steps"]}
    mini = {"schema_version": wf["schema_version"], "workflow": wf["workflow"], "inputs": wf["inputs"], "steps": [steps[f"{loop}-loop"], steps[f"{loop}-applied"]]}
    path = proj.dir.parent / "mini.yml"
    path.write_text(yaml.safe_dump(mini, sort_keys=False), encoding="utf-8")
    bindir = proj.dir.parent / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "ado").write_text(STUB_ADO, encoding="utf-8")
    (bindir / "ado").chmod(0o755)
    proj.env["PATH"] = f"{bindir}{os.pathsep}{proj.env['PATH']}"
    cp = proj.sp("workflow", "run", str(path), "--input", f"story_id={STORY}", "--json")
    return cp, json.loads(cp.stdout)


@pytest.mark.parametrize("loop", LOOPS)
def test_a_supplied_verdict_cannot_approve_a_changed_plan(proj, loop):
    proj.mark("changes")
    cp, out = _mini(proj, loop)
    rid = out["run_id"]
    assert out["status"] == "paused" and out["gate"]["step_id"] == f"approve-{loop}"
    assert proj.resume(rid, f"approve-{loop}", "approve").returncode != 0
    st = proj.state(rid)
    # the apply refused plan B; the run ends FAILED with the gate passed once, never a second time on a plan nobody saw
    assert st["status"] == "failed" and proj.trace() == ["preview", "apply"]


@pytest.mark.parametrize("loop", LOOPS)
def test_an_unchanged_plan_applies_on_the_first_pass(proj, loop):
    cp, out = _mini(proj, loop)
    rid = out["run_id"]
    assert proj.resume(rid, f"approve-{loop}", "approve").returncode == 0
    assert proj.state(rid)["status"] == "completed" and proj.trace() == ["preview", "apply"]


def test_a_plain_resume_of_a_refused_apply_never_applies_the_new_plan(proj):
    proj.mark("changes")
    _, out = _mini(proj, "questions")
    rid = out["run_id"]
    assert proj.resume(rid, "approve-questions", "approve").returncode != 0
    assert proj.sp("workflow", "resume", rid).returncode != 0
    assert proj.state(rid)["status"] == "failed" and proj.trace() == ["preview", "apply"]
