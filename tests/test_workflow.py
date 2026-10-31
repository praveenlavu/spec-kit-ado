import re
import shlex
import subprocess
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
    assert ids == ["pull", "review-spec",
                   "questions-loop", "questions-preview", "approve-questions", "questions-apply", "questions-applied", "plan",
                   "plan-questions-loop", "plan-questions-preview", "approve-plan-questions", "plan-questions-apply", "plan-questions-applied",
                   "wait-po-answers", "plan-with-answers",
                   "answers-questions-loop", "answers-questions-preview", "approve-answers-questions", "answers-questions-apply", "answers-questions-applied",
                   "review-plan", "tasks",
                   "push-loop", "push-preview", "approve-push", "push-apply", "push-applied",
                   "strict-check-implement", "implement",
                   "decisions-loop", "decisions-preview", "approve-decisions", "decisions-apply", "decisions-applied", "strict-verify",
                   "sync-loop", "sync-preview", "approve-sync", "sync-apply", "sync-applied",
                   "gate", "gate-check", "refresh-context", "review", "review-review", "pr-clear", "pr-prepare",
                   "pr-loop", "pr-preview", "approve-pr", "pr-apply", "pr-applied", "wait-po-validation"]


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
    assert len(runs) == 26
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
    assert wf["workflow"]["id"] == "ado-lifecycle" and wf["requires"]["speckit_version"] == ">=0.9.0"
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
    assert loop["type"] == "do-while" and "max_iterations" not in loop  # engine default cap: 10
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
        "if [ -e .ado/plan-decisions.md.new ]; then echo 'spec-kit-ado: the decisions plan kept changing after approval; start a new run' >&2; exit 1; fi")
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
    assert gate["message"].startswith("Approve once the product owner has validated or rejected the posted decisions and you have recorded each with ")
    assert ("`ado decisions <id> --resolve` and committed and pushed the resolved lines to the PR branch before the merge. "
            "In strict mode `ado done` refuses while any is open; if nothing was posted just approve.") in gate["message"]
    assert [s["id"] for s in _wf()["steps"]][-1] == "wait-po-validation"  # after the PR loop: the run still ends at the open PR
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
