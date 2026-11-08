import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import ado
from ado import prs

ROOT = Path(__file__).resolve().parent.parent
STORY = "{{ inputs.story_id }}"


def _wf():
    return yaml.safe_load((ROOT / "workflows/ado-lifecycle/workflow.yml").read_text(encoding="utf-8"))


def _steps():
    """Every step in run order, loop bodies inlined right after their loop step."""
    def flat(steps):
        for s in steps:
            yield s
            yield from flat(s.get("steps", []))
    return list(flat(_wf()["steps"]))


def test_step_order_and_ids():
    ids = [s["id"] for s in _steps()]
    assert ids == ["pull", "stack-check", "review-spec", "check-review-spec",
                   "questions-loop", "questions-preview", "approve-questions", "questions-apply", "questions-applied", "plan",
                   "plan-questions-loop", "plan-questions-preview", "approve-plan-questions", "plan-questions-apply", "plan-questions-applied",
                   "wait-po-answers", "check-wait-po-answers", "sync-po-answers", "plan-with-answers",
                   "answers-questions-loop", "answers-questions-preview", "approve-answers-questions", "answers-questions-apply", "answers-questions-applied",
                   "review-plan", "check-review-plan", "tasks",
                   "push-loop", "push-preview", "approve-push", "push-apply", "push-applied",
                   "strict-check-implement", "implement",
                   "decisions-loop", "decisions-preview", "approve-decisions", "decisions-apply", "decisions-applied", "strict-verify",
                   "sync-loop", "sync-preview", "approve-sync", "sync-apply", "sync-applied",
                   "gate", "gate-check", "refresh-context", "review", "review-review", "check-review-review", "pr-clear", "pr-prepare",
                   "pr-loop", "pr-preview", "approve-pr", "pr-apply", "pr-applied", "wait-po-validation", "sync-po-validation", "check-wait-po-validation"]


def test_workflow_never_closes_the_story():
    # closing happens after merge (CI close-story, or interactive speckit.ado.done), never at PR open
    assert not [s for s in _steps() if "ado done" in s.get("run", "") or s.get("command") == "speckit.ado.done"]


def test_every_apply_shell_preceded_by_gate_with_show_file():
    steps = _steps()
    applies = [i for i, s in enumerate(steps) if "--apply" in s.get("run", "")]
    assert len(applies) == 7
    for i in applies:
        gate = steps[i - 1]
        assert gate["type"] == "gate" and gate["show_file"].startswith(".ado/plan-")
        assert gate["options"] == ["approve", "reject"] and gate["on_reject"] == "abort"
        preview = steps[i - 2]  # a CLI shell step writes the file the gate shows, never an agent
        assert preview["type"] == "shell"
        assert f"--plan-out {gate['show_file']}" in preview["run"] and "--apply" not in preview["run"]
    assert [steps[i]["id"] for i in applies] == ["questions-apply", "plan-questions-apply", "answers-questions-apply", "push-apply", "decisions-apply", "sync-apply", "pr-apply"]


def test_no_gate_message_interpolates():
    gates = [s for s in _steps() if s.get("type") == "gate"]
    assert len(gates) == 12
    for g in gates:
        assert "{{" not in g["message"] and g["options"] == ["approve", "reject"] and g["on_reject"] == "abort"


def test_shell_steps_only_interpolate_story_id():
    runs = [s["run"] for s in _steps() if s.get("type") == "shell"]
    assert len(runs) == 34  # 27 + 5 approvals checks + 2 sync steps (P4-14)
    for run in runs:
        assert all(e.strip() == "inputs.story_id" for e in re.findall(r"\{\{(.*?)\}\}", run))
        assert "{%" not in run


def test_story_id_is_number():
    spec = _wf()["inputs"]["story_id"]
    assert spec["type"] == "number" and spec["required"] is True


def test_versions_match():
    wf = _wf()
    bundle = yaml.safe_load((ROOT / "bundle/bundle.yml").read_text(encoding="utf-8"))
    ext = yaml.safe_load((ROOT / "extension.yml").read_text(encoding="utf-8"))
    v = ado.__version__
    assert wf["workflow"]["id"] == "ado-lifecycle" and wf["requires"]["speckit_version"] == ">=1.2.1"
    assert wf["workflow"]["version"] == bundle["bundle"]["version"] == ext["extension"]["version"] == v
    assert bundle["bundle"]["id"] == "ado" and bundle["bundle"]["role"] == "developer"
    assert bundle["provides"]["extensions"] == [{"id": "ado", "version": v}]
    assert bundle["provides"]["workflows"] == [{"id": "ado-lifecycle", "version": v}]
    assert bundle["tags"] == ext["tags"]


def test_pr_prepare_only_documented():
    text = (ROOT / "commands/pr.md").read_text(encoding="utf-8")
    assert "--prepare-only" in text and "--from-prepared --push --plan-out .ado/plan-pr.md" in text


def test_every_show_file_written_by_preceding_shell_step():
    steps = _steps()
    for i, s in enumerate(steps):
        if "show_file" in s:
            prev = steps[i - 1]
            assert prev["type"] == "shell" and prev["run"].endswith(f"--plan-out {s['show_file']}")
            assert "--apply" not in prev["run"]


def test_pr_preview_matches_pr_apply():
    by_id = {s["id"]: s for s in _steps()}
    assert by_id["pr-preview"]["run"] == f"ado pr create {STORY} --from-prepared --push --plan-out .ado/plan-pr.md"
    assert by_id["pr-apply"]["run"].startswith(
        f"ado pr create {STORY} --from-prepared --push --apply --expect-plan .ado/plan-pr.md || ")


def test_questions_triplet_after_review_spec():
    by_id = {s["id"]: s for s in _steps()}
    assert by_id["questions-preview"]["run"] == f"ado questions {STORY} --post --plan-out .ado/plan-questions.md"
    assert by_id["questions-apply"]["run"].startswith(
        f"ado questions {STORY} --post --apply --expect-plan .ado/plan-questions.md || ")
    ids = [s["id"] for s in _wf()["steps"]]
    assert ids.index("review-spec") < ids.index("questions-loop") < ids.index("plan")
    assert "nothing to post" in by_id["approve-questions"]["message"]


def test_questions_triplet_after_plan_posts_what_the_plan_raised():
    # plan --workflow writes owner: PO Q<n> lines; only `ado questions --post` sends them, so it must run after plan
    by_id = {s["id"]: s for s in _steps()}
    f = ".ado/plan-questions-after-plan.md"
    assert by_id["plan-questions-preview"]["run"] == f"ado questions {STORY} --post --plan-out {f}"
    assert by_id["plan-questions-apply"]["run"].startswith(f"ado questions {STORY} --post --apply --expect-plan {f} || ")
    assert by_id["approve-plan-questions"]["show_file"] == f and "nothing to post" in by_id["approve-plan-questions"]["message"]
    ids = [s["id"] for s in _wf()["steps"]]
    assert ids.index("plan") < ids.index("plan-questions-loop") < ids.index("plan-questions-applied") < ids.index("review-plan") < ids.index("tasks")
    # the engine cannot loop back: a gate to wait at, then plan again to read the PO's replies, before review-plan
    wait, again = by_id["wait-po-answers"], by_id["plan-with-answers"]
    assert wait["type"] == "gate" and wait["options"] == ["approve", "reject"] and wait["on_reject"] == "abort"
    assert "answered" in wait["message"] and "{{" not in wait["message"]
    assert ("Approving without their answers accepts the plan's stated assumptions: the next step reads the replies "
            "into the ledger, records each question still unanswered as `assumed: <assumption>`") in wait["message"]
    assert again["command"] == "speckit.ado.plan" and again["input"]["args"] == f"{STORY} --workflow --close-assumed"
    assert again["integration"] == "{{ inputs.integration }}"
    assert ids.index("plan-questions-applied") < ids.index("wait-po-answers") < ids.index("plan-with-answers") < ids.index("review-plan")


def test_questions_the_second_plan_run_raised_are_posted_behind_a_gate():
    # R3 B1: plan-with-answers can add Q<n> that wait-po-answers never showed; a third post loop sends them
    by_id = {s["id"]: s for s in _steps()}
    f = ".ado/plan-questions-after-answers.md"
    assert by_id["answers-questions-preview"]["run"] == f"ado questions {STORY} --post --plan-out {f}"
    assert by_id["answers-questions-apply"]["run"].startswith(f"ado questions {STORY} --post --apply --expect-plan {f} || ")
    gate = by_id["approve-answers-questions"]
    assert gate["type"] == "gate" and gate["show_file"] == f and gate["on_reject"] == "abort"
    assert "nothing to post" in gate["message"] and "{{" not in gate["message"]
    ids = [s["id"] for s in _wf()["steps"]]
    assert ids.index("plan-with-answers") < ids.index("answers-questions-loop") < ids.index("answers-questions-applied") < ids.index("review-plan")
    assert "answers-questions-loop" in (ROOT / "commands/plan.md").read_text(encoding="utf-8")


def test_sync_triplet_after_implement():
    by_id = {s["id"]: s for s in _steps()}
    assert by_id["sync-preview"]["run"] == f"ado push {STORY} --plan-out .ado/plan-sync.md"
    assert by_id["approve-sync"]["show_file"] == ".ado/plan-sync.md"
    assert by_id["sync-apply"]["run"].startswith(f"ado push {STORY} --apply --expect-plan .ado/plan-sync.md || ")


def test_ado_writing_commands_get_workflow_flag():
    by_id = {s["id"]: s for s in _steps()}
    for sid in ("pull", "plan", "implement"):
        assert by_id[sid]["input"]["args"] == f"{STORY} --workflow"
    assert by_id["plan-with-answers"]["input"]["args"] == f"{STORY} --workflow --close-assumed"


@pytest.mark.parametrize("name", ["pull", "implement"])
def test_command_skips_ado_writes_under_workflow_flag(name):
    text = (ROOT / f"commands/{name}.md").read_text(encoding="utf-8")
    assert "`--workflow`" in text and "skip" in text.lower()


@pytest.mark.parametrize("path", ["rules/hitl.md", "AGENTS.md"])
def test_rules_forbid_ado_writes_inside_workflow(path):
    text = " ".join((ROOT / path).read_text(encoding="utf-8").split())
    assert "Inside the workflow a command never runs an ADO write" in text
    assert "do not wait for chat input" not in text


# ---- PR #1 review fix E: pull/plan never stop to ask inside the workflow ----
NEEDS = "Needs human decision"


@pytest.mark.parametrize("name", ["pull", "plan"])
def test_workflow_mode_records_questions_instead_of_asking(name):
    text = (ROOT / f"commands/{name}.md").read_text(encoding="utf-8")
    assert f"### {NEEDS}" in text and "never stop" in text.lower()


def test_spec_and_plan_gates_point_at_needs_human_decision_list():
    by_id = {s["id"]: s for s in _steps()}
    for gid in ("review-spec", "review-plan"):
        assert f"the open questions in ledger section 2 and the ledger's '{NEEDS}' list before approving" in by_id[gid]["message"]


# ---- F9: command steps need an integration, else the engine says "integration None" ----
def test_every_command_step_has_integration_input():
    wf = _wf()
    assert wf["inputs"]["integration"] == {"type": "string", "default": "auto", "prompt": wf["inputs"]["integration"]["prompt"]}
    assert wf["requires"]["integrations"]["any"]
    commands = [s for s in wf["steps"] if "command" in s]
    assert len(commands) == 8
    for s in commands:
        assert s["integration"] == "{{ inputs.integration }}"
    # never interpolated into a shell step
    assert not [s for s in _steps() if s.get("type") == "shell" and "integration" in str(s)]


# ---- F10: pr-prepare never runs on prepared files left by an earlier run ----
def test_pr_prepare_preceded_by_clearing_prepared_files():
    ids = [s["id"] for s in _steps()]
    clear = _steps()[ids.index("pr-prepare") - 1]
    assert clear["id"] == "pr-clear" and clear["type"] == "shell"
    assert shlex.split(clear["run"]) == ["rm", "-f", prs.PREPARED_ARGS, prs.PREPARED_BODY]


# ---- F12: the gate step runs speckit.ado.gate, so its result is logged to ledger section 4 ----
def test_workflow_gate_step_is_the_gate_command():
    gate = {s["id"]: s for s in _steps()}["gate"]
    assert gate["command"] == "speckit.ado.gate"
    assert gate["integration"] == "{{ inputs.integration }}"
    assert gate["input"]["args"] == f"{STORY} --workflow"  # read-only, but --workflow so it never asks
    assert "type" not in gate and "run" not in gate


# ---- F12 fix: a blocking gate halts the run, but only exit 1 does (missing info never blocks); F29: so does exit 3 (invalid config) ----
def test_gate_check_follows_gate():
    ids = [s["id"] for s in _steps()]
    check = _steps()[ids.index("gate") + 1]
    assert check["id"] == "gate-check" and check["type"] == "shell"
    run = check["run"]
    assert run.startswith("bash <<'EOF'") and 'git diff -z --name-only "$base...HEAD"' in run
    assert "--changed" in run and '"./$f"' in run and "refs/remotes/origin/HEAD" in run
    assert f"ado gate {STORY} --src ." in run and '[ "$code" -ne 1 ] && [ "$code" -ne 3 ]' in run


@pytest.mark.parametrize("code,fails", [(0, False), (1, True), (2, False), (3, True)])
def test_gate_check_fails_on_exit_1_and_3(tmp_path, code, fails):
    ado = tmp_path / "ado"
    ado.write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
    ado.chmod(0o755)
    run = {s["id"]: s for s in _steps()}["gate-check"]["run"].replace(STORY, "1001")
    proc = subprocess.run(["sh", "-c", run], cwd=tmp_path, env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
                          capture_output=True, text=True)
    assert (proc.returncode != 0) == fails


def _run_gate_check(tmp_path, ado_script, cwd):
    ado = tmp_path / "bin" / "ado"
    ado.parent.mkdir(exist_ok=True)
    ado.write_text(ado_script, encoding="utf-8")
    ado.chmod(0o755)
    run = {s["id"]: s for s in _steps()}["gate-check"]["run"].replace(STORY, "1001")
    return subprocess.run(["sh", "-c", run], cwd=cwd, env={"PATH": f"{ado.parent}:/usr/bin:/bin"},
                          capture_output=True, text=True)


def test_gate_check_passes_changed_files_nul_safely(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    run = lambda *a: subprocess.run([*git, *a], cwd=repo, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    run("add", "."); run("commit", "-qm", "base")
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    run("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    for name in ("-x.txt", "with space.txt"):
        (repo / name).write_text("n")
    run("add", "."); run("commit", "-qm", "change")
    out = tmp_path / "argv"
    proc = _run_gate_check(tmp_path, f'#!/bin/sh\nprintf "%s\\n" "$@" > {out}\nexit 0\n', repo)
    assert proc.returncode == 0
    assert out.read_text().splitlines() == ["gate", "1001", "--src", ".", "--base", "origin/main", "--changed", "./-x.txt", "./with space.txt"]


def test_gate_check_without_a_resolvable_base_runs_gate_without_changed(tmp_path):
    out = tmp_path / "argv"
    proc = _run_gate_check(tmp_path, f'#!/bin/sh\nprintf "%s\\n" "$@" > {out}\nexit 0\n', tmp_path)
    assert proc.returncode == 0 and "--changed" not in out.read_text() and "--base" in out.read_text()
    assert "changed files unavailable" in proc.stderr


def test_gate_check_exit_1_halts_even_if_ado_reads_stdin(tmp_path):
    proc = _run_gate_check(tmp_path, "#!/bin/sh\ncat > /dev/null\nexit 1\n", tmp_path)
    assert proc.returncode != 0


@pytest.mark.parametrize("name", ["gate", "review"])
def test_workflow_passes_workflow_flag_to_gate_and_review(name):
    assert {s["id"]: s for s in _steps()}[name]["input"]["args"] == f"{STORY} --workflow"


@pytest.mark.parametrize("name", ["gate", "review"])
def test_command_never_asks_under_workflow_flag(name):
    text = (ROOT / f"commands/{name}.md").read_text(encoding="utf-8")
    assert "`--workflow`" in text and f"### {NEEDS}" in text and "never ask" in text.lower()
    assert "refs/remotes/origin/HEAD" in text and "section 4" in text


def test_review_gate_rejects_on_incomplete():
    gate = {s["id"]: s for s in _steps()}["review-review"]
    assert gate["message"].startswith("Review the findings logged in the ledger before opening the PR. Reject if implement reported INCOMPLETE tasks:") and "{{" not in gate["message"]


def test_implement_command_documents_incomplete_line():
    assert "INCOMPLETE: <task ids>" in (ROOT / "commands" / "implement.md").read_text()


# ---- F22: an apply only ever applies the plan its gate showed; a resume re-previews ----
LOOPS = {"questions": ".ado/plan-questions.md", "plan-questions": ".ado/plan-questions-after-plan.md", "push": ".ado/plan-push.md", "decisions": ".ado/plan-decisions.md", "sync": ".ado/plan-sync.md", "pr": ".ado/plan-pr.md"}


def _ado_cmd(run):
    return shlex.split(run.split(" || ")[0])


@pytest.mark.parametrize("name,plan", LOOPS.items())
def test_each_triplet_is_a_do_while_on_plan_changed(name, plan):
    top = {s["id"]: s for s in _wf()["steps"]}
    loop = top[f"{name}-loop"]
    assert loop["type"] == "do-while" and loop["max_iterations"] == 1  # one pass: a supplied verdict must not re-approve a changed plan
    assert [s["id"] for s in loop["steps"]] == [f"{name}-preview", f"approve-{name}", f"{name}-apply"]
    assert loop["condition"] == f"{{{{ steps.{name}-apply.output.stderr | contains('plan changed since approval') }}}}"
    preview, gate, apply = loop["steps"]
    assert gate["show_file"] == plan and _ado_cmd(preview["run"])[-2:] == ["--plan-out", plan]
    assert _ado_cmd(apply["run"])[-3:] == ["--apply", "--expect-plan", plan]
    # identical flags apart from the plan-file flags (F11 ruling: pr-preview carries --push too)
    assert _ado_cmd(preview["run"])[:-2] == _ado_cmd(apply["run"])[:-3]
    ids = [s["id"] for s in _wf()["steps"]]
    assert ids[ids.index(f"{name}-loop") + 1] == f"{name}-applied"


def _sh(run, tmp_path, ado_exit, writes_new):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "ado").write_text("#!/bin/sh\n" + ("mkdir -p .ado; : > .ado/plan-push.md.new\n" if writes_new else "")
                                 + f"exit {ado_exit}\n", encoding="utf-8")
    (bin_dir / "ado").chmod(0o755)
    return subprocess.run(["sh", "-c", run.replace(STORY, "1001")], cwd=tmp_path,
                          env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, capture_output=True, text=True).returncode


@pytest.mark.parametrize("code,writes_new,ok", [(0, False, True), (1, False, False), (2, False, False), (2, True, True)])
def test_apply_step_swallows_only_the_plan_changed_refusal(tmp_path, code, writes_new, ok):
    # exit 2 + <file>.new = plan changed: the step ends cleanly so the loop re-previews.
    # Any other failure (exit 1 partial write, exit 2 refusal for another reason) stops the run.
    run = {s["id"]: s for s in _steps()}["push-apply"]["run"]
    assert (_sh(run, tmp_path, code, writes_new) == 0) == ok


@pytest.mark.parametrize("left_new", [False, True])
def test_applied_step_stops_the_run_only_while_the_plan_still_differs(tmp_path, left_new):
    if left_new:
        (tmp_path / ".ado").mkdir()
        (tmp_path / ".ado" / "plan-push.md.new").write_text("fresh plan")
    run = {s["id"]: s for s in _steps()}["push-applied"]["run"]
    assert (_sh(run, tmp_path, 0, False) == 0) == (not left_new)


def test_approve_gate_messages_constant_and_truthful():
    by_id = {s["id"]: s for s in _steps()}
    for gid in ("approve-questions", "approve-plan-questions", "approve-push", "approve-decisions", "approve-sync", "approve-pr"):
        msg = by_id[gid]["message"]
        assert "{{" not in msg and "full body" not in msg
        assert "If the preview starts with ⚠, open the file and read it in full before approving." in msg


def test_story_discussion_is_refreshed_right_before_the_review():
    # R3 B5: the review used a stale .ado/context file and missed a later PO comment
    by_id = {s["id"]: s for s in _steps()}
    assert by_id["refresh-context"]["type"] == "shell" and by_id["refresh-context"]["run"].startswith(f"ado comments {STORY} > /dev/null")
    ids = [s["id"] for s in _wf()["steps"]]
    assert ids.index("gate-check") < ids.index("refresh-context") == ids.index("review") - 1
    text = " ".join((ROOT / "commands/review.md").read_text(encoding="utf-8").split())
    assert "the `refresh-context` step has just done it" in text and "run `ado comments <id>`" in text and "story discussion not refreshed" in text


# ---- V3-15: strict steps and the decisions triplet; every new step is a no-op the CLI answers in advisory mode ----
def test_decisions_triplet_shape():
    by_id = {s["id"]: s for s in _steps()}
    f = ".ado/plan-decisions.md"
    assert by_id["decisions-preview"]["run"] == f"ado decisions {STORY} --post --if-any --plan-out {f}"
    assert by_id["decisions-apply"]["run"].startswith(f"ado decisions {STORY} --post --if-any --apply --expect-plan {f} || ")
    assert by_id["decisions-apply"]["run"].endswith("{ [ $? -eq 2 ] && [ -e .ado/plan-decisions.md.new ]; }")  # the standard guard
    gate = by_id["approve-decisions"]
    assert gate["type"] == "gate" and gate["show_file"] == f and gate["options"] == ["approve", "reject"] and gate["on_reject"] == "abort"
    assert "{{" not in gate["message"] and "nothing to post" in gate["message"]
    assert "If the preview starts with ⚠, open the file and read it in full before approving." in gate["message"]
    assert by_id["decisions-applied"]["run"] == (
        "if [ -e .ado/plan-decisions.md.new ]; then echo 'spec-kit-ado: the decisions plan changed after approval; start a new run' >&2; exit 1; fi")
    assert by_id["strict-check-implement"]["run"] == f"ado strict check {STORY} --phase implement"
    assert by_id["strict-verify"]["run"] == f"ado strict verify {STORY}"
    assert by_id["strict-verify"]["timeout"] == 7200  # the engine's shell default is 300 s: it would kill the test runs mid-way
    assert by_id["strict-check-implement"]["type"] == by_id["strict-verify"]["type"] == "shell"
    ids = [s["id"] for s in _wf()["steps"]]
    assert ids.index("push-applied") < ids.index("strict-check-implement") == ids.index("implement") - 1
    assert (ids.index("implement") < ids.index("decisions-loop") < ids.index("decisions-applied") < ids.index("strict-verify")
            < ids.index("sync-loop") < ids.index("gate"))
    assert ids.index("decisions-applied") == ids.index("decisions-loop") + 1 and ids.index("strict-verify") == ids.index("decisions-applied") + 1


def test_wait_po_validation_gate_message():
    gate = {s["id"]: s for s in _steps()}["wait-po-validation"]
    assert gate["type"] == "gate" and gate["options"] == ["approve", "reject"] and gate["on_reject"] == "abort"
    assert "show_file" not in gate and "{{" not in gate["message"]
    assert gate["message"].startswith("Approve once the product owner has validated or rejected the posted decisions. ")
    assert "Terminal mode: record each verdict first with `ado decisions <id> --resolve` in your own terminal." in gate["message"]
    assert ("Comment mode (approvers configured; `--resolve` is refused): the approvers' `/approve D<n>` and `/reject D<n>` comments are recorded by "
            "`ado decisions <id> --sync` in the next step.") in gate["message"]
    assert ("commit and push the recorded lines to the PR branch before the merge. "
            "In strict mode `ado done` refuses while any is open; if nothing was posted just approve.") in gate["message"]
    ids = [s["id"] for s in _wf()["steps"]]
    assert ids[-3:] == ["wait-po-validation", "sync-po-validation", "check-wait-po-validation"]  # after the PR loop: the run still ends at the open PR
    assert not [s for s in _steps() if s.get("command") == "speckit.ado.done" or "ado done" in s.get("run", "")]


def _cli_argv(run, story=1001):
    argv = shlex.split(run.split(" || ")[0].replace(STORY, str(story)))
    assert argv[0] == "ado"
    return argv[1:]


def test_strict_steps_are_noops_in_advisory(tmp_path, monkeypatch, capsys):
    # the YAML stays free of conditionals: in an advisory project (no mode, no decisions file) every new step is exit 0 and a note
    import json
    from ado import __main__ as m
    from tests.conftest import FakeClient
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setenv("ADO_TOKEN", "t")
    made = []
    monkeypatch.setattr(m, "AdoClient", lambda cfg: made.append(FakeClient(cfg)) or made[-1])
    by_id = {s["id"]: s for s in _steps()}
    for sid in ("strict-check-implement", "strict-verify"):
        assert m.main(_cli_argv(by_id[sid]["run"])) == 0
        assert json.loads(capsys.readouterr().out)["note"] == "mode is advisory"
    assert m.main(_cli_argv(by_id["decisions-preview"]["run"])) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "none"
    assert "nothing to post" in (tmp_path / ".ado/plan-decisions.md").read_text(encoding="utf-8")
    assert m.main(_cli_argv(by_id["decisions-apply"]["run"])) == 0  # --expect-plan matches the empty plan: nothing to apply
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "none" and out["applied"] is True and out["comment_id"] is None and "no decisions file" in out["note"]
    assert not (tmp_path / ".ado/plan-decisions.md.new").exists() and not (tmp_path / "dod").exists()
    assert not [c for fc in made for c in fc.calls if c["method"] != "GET"]  # no ADO write, and nothing created locally


def test_only_strict_verify_sets_a_step_timeout():
    # engine defaults: shell 300 s (fine for the ado calls, which are local or one HTTP round trip), a command step has none (not captured)
    assert [s["id"] for s in _steps() if "timeout" in s] == ["strict-verify"]


def test_stack_check_follows_pull_and_precedes_review_spec():
    ids = [s["id"] for s in _steps()]
    assert ids[:3] == ["pull", "stack-check", "review-spec"]
    step = _steps()[1]
    assert step["type"] == "shell" and step["run"] == "ado stack show --check" and "timeout" not in step


def test_stack_check_is_a_noop_in_advisory(tmp_path, monkeypatch, capsys):
    import json
    from ado import __main__ as m
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    run = {s["id"]: s for s in _steps()}["stack-check"]["run"]
    assert m.main(shlex.split(run)[1:]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "none"


@pytest.mark.parametrize("path", ["workflows/ado-lifecycle/README.md", "workflows/ado-lifecycle/CHANGELOG.md"])
def test_workflow_docs_describe_stack_check(path):
    text = " ".join((ROOT / path).read_text(encoding="utf-8").split())
    assert "stack-check" in text and "ado stack show --check" in text and "speckit.ado.stack" in text


# ---- piece 3 (P3-11): business rules change prompts and docs, never the workflow ----
BASE_WORKFLOW_SHA256 = "a9daf4db0357663a2cda5d777cfb29f538d2bc8fe13903bcf6c3241901574ec0"  # workflow.yml at the P4-14 base, version and speckit_version lines blanked (R-1 raised the floor); F7 then dropped "; F11 ruling" from one comment (no step, input or shell line); v0.3.1 then capped the seven loops at one pass and reworded the seven *-applied messages and one comment block (no step, input or gate)


def test_workflow_unchanged_by_rules():
    import hashlib
    raw = (ROOT / "workflows/ado-lifecycle/workflow.yml").read_bytes()
    norm = re.sub(rb'(?m)^  (version|speckit_version): "[^"]*"$', rb'  \1: "X"', raw)  # the release bump changes the version and floor lines, nothing else
    assert hashlib.sha256(norm).hexdigest() == BASE_WORKFLOW_SHA256, "workflow.yml changed: business rules add no step, input or shell line"
    # the step graph is the one before the rules: no step id, shell line, gate message or argument names a rule
    steps = _steps()
    assert len(steps) == 61  # 54 before piece 4, + 5 checks + 2 syncs (P4-14)
    for s in steps:
        assert "rule" not in str(s).lower().replace("rules/", "").replace("review_block_on", ""), s["id"]
    assert not [s for s in steps if "ado rules" in s.get("run", "")]


def _upstream_engine():
    """Spec Kit's workflow engine. Missing: skip locally, but fail under CI (which installs Spec Kit), like `conftest.require_specify`."""
    try:
        import specify_cli.workflows.engine as engine  # noqa: PLC0415
    except ImportError:
        (pytest.fail if os.environ.get("CI") else pytest.skip)("Spec Kit (specify_cli.workflows.engine) is not importable")
    return engine


def test_workflow_passes_the_upstream_validator():
    engine = _upstream_engine()
    definition = engine.WorkflowDefinition.from_yaml(str(ROOT / "workflows/ado-lifecycle/workflow.yml"))
    assert engine.validate_workflow(definition) == []


@pytest.mark.parametrize("ci,outcome", [("true", pytest.fail.Exception), (None, pytest.skip.Exception)])
def test_missing_upstream_engine_fails_under_ci_and_skips_locally(monkeypatch, ci, outcome):
    monkeypatch.setitem(sys.modules, "specify_cli.workflows.engine", None)  # makes the import raise ImportError
    monkeypatch.delenv("CI", raising=False)
    if ci:
        monkeypatch.setenv("CI", ci)
    with pytest.raises(outcome):
        _upstream_engine()


# ---- piece 4 (P4-14): verdict inputs, approvals checks and the sync steps ----
GATE_IDS = ["review-spec", "approve-questions", "approve-plan-questions", "wait-po-answers", "approve-answers-questions", "review-plan", "approve-push",
            "approve-decisions", "approve-sync", "review-review", "approve-pr", "wait-po-validation"]
CHECKED = ["review-spec", "review-plan", "review-review", "wait-po-answers", "wait-po-validation"]


def _check_run(gate):
    return f"ado approvals check {STORY} --gate {gate}"


def test_every_gate_has_own_verdict_input_with_enum():
    gates = [s for s in _steps() if s.get("type") == "gate"]
    assert [g["id"] for g in gates] == GATE_IDS
    inputs = _wf()["inputs"]
    names = [g["verdict_input"] for g in gates]
    assert names == ["verdict_" + g.replace("-", "_") for g in GATE_IDS] and len(set(names)) == 12
    for n in names:
        assert inputs[n]["type"] == "string" and inputs[n]["enum"] == ["", "approve", "reject"] and inputs[n]["default"] == ""
        assert not inputs[n].get("required")
    assert sorted(k for k in inputs if k.startswith("verdict_")) == sorted(names)  # no verdict input without a gate
    for g in gates:  # the gate itself is unchanged: an empty input prompts at a TTY and otherwise pauses
        assert g["options"] == ["approve", "reject"] and g["on_reject"] == "abort"


def test_verdict_input_names_agree_with_the_poller():
    from ado import approvals, status
    assert list(approvals.GATES) == GATE_IDS == list(status.SHIPPED_GATES)
    argv = approvals.resume_argv("20260101-000000-abcdef12", "review-plan", "approve")
    pairs = [argv[i + 1] for i in range(len(argv)) if argv[i] == "--input"]
    assert argv[:4] == ["specify", "workflow", "resume", "20260101-000000-abcdef12"] and len(pairs) == 12 and argv.count("--input") == 12
    gates = {s["id"]: s["verdict_input"] for s in _steps() if s.get("type") == "gate"}
    assert sorted(p.split("=")[0] for p in pairs) == sorted(gates.values())  # every input the poller sets exists, and none is missing
    assert "verdict_review_plan=approve" in pairs and sum(p.endswith("=") for p in pairs) == 11
    for g in GATE_IDS:
        assert approvals.resume_argv("20260101-000000-abcdef12", g, "reject")[5] == f"{gates[g]}=reject"


def test_check_step_follows_each_review_and_wait_gate():
    by_id = {s["id"]: s for s in _steps()}
    ids = [s["id"] for s in _steps()]
    expect_next = {"review-spec": "check-review-spec", "review-plan": "check-review-plan", "review-review": "check-review-review",
                   "wait-po-answers": "check-wait-po-answers", "wait-po-validation": "sync-po-validation"}
    for g, nxt in expect_next.items():
        assert ids[ids.index(g) + 1] == nxt, g
    checks = [s for s in _steps() if s.get("type") == "shell" and "approvals check" in s["run"]]
    assert sorted(c["id"] for c in checks) == sorted("check-" + g for g in CHECKED) and len(checks) == 5
    for g in CHECKED:
        c = by_id["check-" + g]
        assert c["type"] == "shell" and c["run"] == _check_run(g) and "timeout" not in c
    # a check never follows a write gate (the apply right after it requires the comment itself) and never replaces one
    assert all(g in CHECKED for g in (c["run"].split("--gate ")[1] for c in checks))
    assert ids.index("check-wait-po-validation") == ids.index("sync-po-validation") + 1 == ids.index("wait-po-validation") + 2


def test_sync_steps_placed():
    by_id = {s["id"]: s for s in _steps()}
    ids = [s["id"] for s in _steps()]
    assert ids[ids.index("wait-po-answers"):ids.index("wait-po-answers") + 4] == ["wait-po-answers", "check-wait-po-answers", "sync-po-answers", "plan-with-answers"]
    assert ids[-3:] == ["wait-po-validation", "sync-po-validation", "check-wait-po-validation"]
    qs, ds = by_id["sync-po-answers"], by_id["sync-po-validation"]
    assert qs["type"] == ds["type"] == "shell" and "timeout" not in qs and "timeout" not in ds
    assert f"ado questions {STORY} --sync" in qs["run"] and f"ado decisions {STORY} --sync" in ds["run"]
    for s in (qs, ds):
        assert "--apply" not in s["run"] and "--post" not in s["run"] and "--resolve" not in s["run"]  # a local write: no ADO write, no gate
    assert [s["id"] for s in _steps() if "--sync" in s.get("run", "")] == ["sync-po-answers", "sync-po-validation"]


def test_no_shell_step_reads_a_verdict_or_the_model():
    for s in _steps():
        if s.get("type") == "shell":
            assert "verdict" not in s["run"] and "review_model" not in s["run"], s["id"]


def test_review_model_input_feeds_only_the_review_step():
    spec = _wf()["inputs"]["review_model"]
    assert spec["type"] == "string" and spec["default"] == "" and not spec.get("required") and "different model" in spec["prompt"]
    with_model = [s["id"] for s in _steps() if "model" in s]
    assert with_model == ["review"] and {s["id"]: s for s in _steps()}["review"]["model"] == "{{ inputs.review_model }}"


def test_upstream_gate_step_honours_the_verdict_contract(monkeypatch):
    """The pinned Spec Kit's own gate step: an empty verdict pauses without a TTY, approve/reject (any case) decide, anything else fails."""
    _upstream_engine()
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # not a TTY, whatever runs the tests
    from specify_cli.workflows.base import StepContext, StepStatus  # noqa: PLC0415
    from specify_cli.workflows.step.gate import GateStep  # noqa: PLC0415
    gate = {s["id"]: s for s in _steps()}["review-plan"]
    for value, status, choice in (("", StepStatus.PAUSED, None), ("approve", StepStatus.COMPLETED, "approve"), ("reject", StepStatus.FAILED, "reject"),
                                  ("maybe", StepStatus.FAILED, None)):
        res = GateStep().execute(gate, StepContext(inputs={gate["verdict_input"]: value}))
        assert res.status == status and res.output["choice"] == choice
        if value == "reject":
            assert res.output["aborted"] is True


def _ado_stub(tmp_path, body):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    (d / "ado").write_text("#!/bin/sh\n" + body, encoding="utf-8")
    (d / "ado").chmod(0o755)
    return d


def _run_step(sid, tmp_path, ado_body, story="1001"):
    d = _ado_stub(tmp_path, ado_body)
    run = {s["id"]: s for s in _steps()}[sid]["run"].replace(STORY, story)
    return subprocess.run(["sh", "-c", run], cwd=tmp_path, env={"PATH": f"{d}:/usr/bin:/bin"}, capture_output=True, text=True)


def test_sync_steps_are_the_two_exact_one_liners():
    by_id = {s["id"]: s for s in _steps()}
    assert by_id["sync-po-answers"]["run"] == f"ado questions {STORY} --sync --if-comment-mode"
    assert by_id["sync-po-validation"]["run"] == f"ado decisions {STORY} --sync --if-comment-mode"  # no exit-code juggling, no stderr matching


def test_sync_steps_pass_the_story_id_and_nothing_else(tmp_path):
    for sid, verb in (("sync-po-answers", "questions"), ("sync-po-validation", "decisions")):
        out = tmp_path / f"{sid}.argv"
        assert _run_step(sid, tmp_path, f'printf "%s\\n" "$@" > {out}\n', story="1001").returncode == 0
        assert out.read_text().splitlines() == [verb, "1001", "--sync", "--if-comment-mode"]


def _cli_env(tmp_path, monkeypatch, approvers=(), approval="terminal", ado_client=None):
    """A project with the config and a trusted() that says what the target branch says; an AdoClient that must not be built unless `ado_client` is given."""
    import json
    from ado import __main__ as m
    from ado.config import Trust
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    monkeypatch.setattr(m, "trusted", lambda repo=".", base=None: Trust(list(approvers), approval, {}))
    built = []

    def make(cfg):
        if ado_client is None:
            pytest.fail("no ADO client may be built here")
        built.append(ado_client(cfg))
        return built[-1]
    monkeypatch.setattr(m, "AdoClient", make)
    return m, built


GUID = "11111111-2222-4333-8444-555555555555"


def _fake(cfg):
    from tests.conftest import FakeClient
    return FakeClient(cfg, responses=[])


@pytest.mark.parametrize("verb", ["questions", "decisions"])
def test_if_comment_mode_is_a_noop_in_terminal_mode_even_with_approvers(tmp_path, monkeypatch, capsys, verb):
    # approvers set + approval left "terminal": a bypass-capable token (the usual admin PAT) must not matter, so nothing is read: no token, no client, no file
    import json
    m, built = _cli_env(tmp_path, monkeypatch, approvers=(GUID,), approval="terminal")
    assert m.main([verb, "1001", "--sync", "--if-comment-mode"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"id": 1001, "synced": False, "note": m.E_NOT_COMMENT_MODE} and "terminal mode" in out["note"]
    assert not built and sorted(p.name for p in tmp_path.iterdir()) == ["ado.config.json"]


@pytest.mark.parametrize("verb", ["questions", "decisions"])
def test_without_the_flag_sync_is_as_before(tmp_path, monkeypatch, capsys, verb):
    m, built = _cli_env(tmp_path, monkeypatch, approvers=(), approval="terminal", ado_client=_fake)
    assert m.main([verb, "1001", "--sync"]) == 2  # no approvers on the target branch
    assert "none are configured" in capsys.readouterr().err and len(built) == 1


def test_flag_needs_sync(tmp_path, monkeypatch, capsys):
    m, _ = _cli_env(tmp_path, monkeypatch)
    for verb in ("questions", "decisions"):
        with pytest.raises(SystemExit) as e:
            m.main([verb, "1001", "--if-comment-mode"])
        assert e.value.code == 2
    assert "--if-comment-mode needs --sync" in capsys.readouterr().err


def _decisions_file(tmp_path):
    (tmp_path / "dod").mkdir(exist_ok=True)
    return tmp_path / "dod" / "Decisions_1001.md"


def test_decisions_flag_in_comment_mode_stops_on_every_unreadable_file(tmp_path, monkeypatch, capsys):
    # a symlink, a 2 MB file and a non-UTF-8 file are named refusals (exit 2: the step stops the run), never "nothing to sync"
    m, built = _cli_env(tmp_path, monkeypatch, approvers=(GUID,), approval="comment", ado_client=_fake)
    path = _decisions_file(tmp_path)
    real = tmp_path / "real.md"
    real.write_text("# Decisions\n", encoding="utf-8")
    cases = {"symlink": lambda: path.symlink_to(real), "oversized": lambda: path.write_bytes(b"x" * 2_000_000),
             "not-utf8": lambda: path.write_bytes(b"\xff\xfe\xfa not utf-8"), "dangling-symlink": lambda: path.symlink_to(tmp_path / "nowhere.md")}
    for name, make in cases.items():
        if os.path.lexists(path):
            path.unlink()
        make()
        assert m.main(["decisions", "1001", "--sync", "--if-comment-mode"]) == 2, name
        assert "cannot read the decisions file" in capsys.readouterr().err, name
    assert not [c for b in built for c in b.calls]  # the refusal came before any request


def test_decisions_flag_in_comment_mode_with_no_file_is_a_noop(tmp_path, monkeypatch, capsys):
    import json
    m, built = _cli_env(tmp_path, monkeypatch, approvers=(GUID,), approval="comment")
    assert m.main(["decisions", "1001", "--sync", "--if-comment-mode"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"id": 1001, "synced": False, "note": m.E_NO_DECISIONS_FILE} and not built  # no token, no client
    assert not (tmp_path / "dod").exists()


def test_questions_flag_in_comment_mode_never_skips_a_missing_ledger(tmp_path, monkeypatch, capsys):
    m, built = _cli_env(tmp_path, monkeypatch, approvers=(GUID,), approval="comment", ado_client=_fake)
    assert m.main(["questions", "1001", "--sync", "--if-comment-mode"]) == 2
    assert "cannot read the ledger" in capsys.readouterr().err


@pytest.mark.parametrize("verb", ["questions", "decisions"])
def test_if_comment_mode_with_an_unreadable_target_config_stops_the_run(tmp_path, monkeypatch, capsys, verb):
    # the target branch's config cannot be read (ConfigInvalid): that is a refusal (exit 2), never "not comment mode, skip"
    from ado.config import ConfigInvalid
    m, built = _cli_env(tmp_path, monkeypatch)
    def broken(repo=".", base=None):
        raise ConfigInvalid("the config on the target branch is not valid JSON")
    monkeypatch.setattr(m, "trusted", broken)
    assert m.main([verb, "1001", "--sync", "--if-comment-mode"]) == 2
    cap = capsys.readouterr()
    assert "not valid JSON" in cap.err and cap.out == "" and not built and sorted(p.name for p in tmp_path.iterdir()) == ["ado.config.json"]


@pytest.mark.parametrize("verb", ["questions", "decisions"])
def test_if_comment_mode_with_invalid_json_on_origin_head_stops_the_run(tmp_path, monkeypatch, capsys, git_env, verb):
    # the real trusted(): a real git repo whose origin/HEAD holds an ado.config.json that is not JSON (the working tree's is fine)
    from ado import __main__ as m
    from tests.conftest import git
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "ado.config.json").write_text("{not json", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "target")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    (repo / "ado.config.json").write_text('{"organization": "contoso", "project": "Fabrikam"}', encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    built = []
    monkeypatch.setattr(m, "AdoClient", lambda cfg: built.append(cfg) or pytest.fail("no ADO client may be built here"))
    before = sorted(p.name for p in repo.rglob("*") if ".git" not in p.parts)
    assert m.main([verb, "1001", "--sync", "--if-comment-mode"]) == 2
    cap = capsys.readouterr()
    assert cap.err.startswith("error:") and cap.out == "" and not built
    assert sorted(p.name for p in repo.rglob("*") if ".git" not in p.parts) == before


def test_comment_mode_with_no_approvers_still_stops_the_run(tmp_path, monkeypatch, capsys):
    m, _ = _cli_env(tmp_path, monkeypatch, approvers=(), approval="comment", ado_client=_fake)
    _decisions_file(tmp_path).write_text("# Decisions\n", encoding="utf-8")  # a missing decisions file is the one thing the flag skips
    for verb in ("questions", "decisions"):
        assert m.main([verb, "1001", "--sync", "--if-comment-mode"]) == 2
        assert "none are configured" in capsys.readouterr().err


def test_terminal_mode_behaviour_unchanged(tmp_path, tmp_path_factory, monkeypatch, capsys):
    """Default config (no approval key = terminal): the five checks are no-ops that never build a client or read ADO, and the sync steps, run for real,
    end the step with exit 0, write nothing and make no request."""
    import json
    from ado import __main__ as m
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    monkeypatch.setattr(m, "AdoClient", lambda cfg: pytest.fail("a terminal-mode check must not build an ADO client"))
    by_id = {s["id"]: s for s in _steps()}
    for g in CHECKED:
        assert m.main(_cli_argv(by_id["check-" + g]["run"])) == 0
        assert json.loads(capsys.readouterr().out)["mode"] == "terminal"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ado.config.json"]  # the checks wrote nothing
    # the sync steps through the real CLI in a subprocess, in the shell the engine uses
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ado").write_text(f'#!/bin/sh\nexec "{sys.executable}" -m ado "$@"\n', encoding="utf-8")
    (bin_dir / "ado").chmod(0o755)
    home = tmp_path_factory.mktemp("home")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "PYTHONPATH": str(ROOT / "src"), "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    before = sorted(p.name for p in tmp_path.rglob("*"))
    for sid in ("sync-po-answers", "sync-po-validation"):
        proc = subprocess.run(["sh", "-c", by_id[sid]["run"].replace(STORY, "1001")], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert "terminal mode" in proc.stdout  # the fixed note, and no refusal
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
