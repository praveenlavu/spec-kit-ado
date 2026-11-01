"""`ado status` (P4-4): the first unmet stage of a story and the one command that moves it on.

Every scenario is a real temporary git repository with a ledger, a spec dir and commits (the gate, review and verification record are judged
against git) and a `Board` that answers the few ADO reads status makes and fails the test on any write."""
import hashlib
import json
import os

import pytest

from ado import decisions, dod, questions, rules, status
from ado.__main__ import main
from ado.client import AdoError
from ado.config import Config, ConfigError
from tests.conftest import approve, commit, git, profile, review_md

ID = 1001
ME = "11111111-2222-3333-4444-555555555555"
UNTRUSTED = "ZZ-ADO-TEXT"
# Recorded live on the smoke projects (GUIDs made up here): an Azure Repos PR (story 33, e2e BR-03) and, with the Azure Boards GitHub app authorised in
# the project, a GitHub PR and its commit (story 107, 2026-11-01). Only the two pull-request urls count; a branch, a commit and look-alikes do not.
PROJ, REPO, REPO2 = "0a1b2c3d-1111-2222-3333-444455556666", "7f8e9d0c-aaaa-bbbb-cccc-ddddeeeeffff", "12345678-9abc-def0-1234-56789abcdef0"
AZURE_PR = {"rel": "ArtifactLink", "url": f"vstfs:///Git/PullRequestId/{PROJ}%2F{REPO}%2F7", "attributes": {"name": "Pull Request"}}
GITHUB_PR = {"rel": "ArtifactLink", "url": f"vstfs:///GitHub/PullRequest/{REPO2}%2f12", "attributes": {"name": "GitHub Pull Request"}}
GITHUB_COMMIT = {"rel": "ArtifactLink", "url": f"vstfs:///GitHub/Commit/{REPO2}%2f0123456789abcdef0123456789abcdef01234567", "attributes": {"name": "GitHub Commit"}}
BRANCH = {"rel": "ArtifactLink", "url": f"vstfs:///Git/Ref/{PROJ}%2F{REPO}%2FGBfeature%2Fab1001-x", "attributes": {"name": "Branch"}}
CHILD = {"id": 50, "title": "[sk:T001] Do it", "state": "Done"}
TASKS = {"ticked": "- [x] T001 Do it\n", "open": "- [ ] T001 Do it\n"}
_BODY = ["Customers earn points."]
RULE_HASH = rules.rule_hash(1, "approved", _BODY)
RULES_FILE = rules.render_file({"id": 900, "fields": {"System.Title": "Points", "System.WorkItemType": "Feature"}},
                               {"found": True, "rules": [{"id": "BR-900-1", "n": 1, "status": "approved", "body": _BODY, "hash": RULE_HASH}],
                                "duplicates": [], "problems": [], "notes": []}, "contoso", "Fabrikam", "2026-11-03", "0.3.0")
COMMAND = "PYTHONPATH=src python3 -m pytest -q"  # what the approved test profile composes
NEXT = {  # the exact next command of every stage
    "ledger": f"speckit.ado.pull {ID}", "questions-posted": f"ado questions {ID} --post --plan-out .ado/plan-questions.md",
    "questions-answered": f"speckit.ado.plan {ID}", "plan": f"speckit.ado.plan {ID}", "tasks": f"speckit.ado.tasks {ID}",
    "board-tasks": f"ado push {ID} --plan-out .ado/plan-push.md", "tasks-ticked": f"speckit.ado.implement {ID}",
    "board-sync": f"ado push {ID} --plan-out .ado/plan-sync.md",
    "decisions-posted": f"ado decisions {ID} --post --if-any --plan-out .ado/plan-decisions.md",
    "verified": f"ado strict verify {ID}", "gate": f"speckit.ado.gate {ID}", "review": f"speckit.ado.review {ID}",
    "pr-link": f"speckit.ado.pr {ID}", "merged": "git fetch origin", "done-state": f"speckit.ado.done {ID}",
    "decisions-closed": f"ado decisions {ID}",
}


class Board:
    """The ADO reads status makes, routed by path. Anything but a GET (or the WIQL POST that lists children) fails the test."""

    def __init__(self, config, state="Done", relations=(AZURE_PR,), children=(CHILD,), comments=(), fail=None):
        self.config, self.state, self.relations, self.children, self.comments, self.fail = config, state, list(relations), list(children), list(comments), fail
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path))
        if self.fail:
            raise self.fail
        if "connectionData" in path:
            return {"authenticatedUser": {"id": ME, "descriptor": "aad.abc"}}
        if "/comments?" in path:
            return {"comments": self.comments}
        if "/wit/workitems?ids=" in path:
            return {"value": [{"id": c["id"], "fields": {"System.Title": c["title"], "System.State": c["state"]}} for c in self.children]}
        if "/wit/workitems/" in path:
            return {"id": ID, "fields": {"System.State": self.state, "System.Title": UNTRUSTED}, "relations": self.relations}
        raise AssertionError(f"unexpected read {path}")

    def post(self, path, body, **kw):
        self.calls.append(("POST", path))
        assert path.split("?")[0].endswith("/wit/wiql"), f"status wrote to {path}"
        if self.fail:
            raise self.fail
        return {"workItems": [{"id": c["id"]} for c in self.children]}

    def patch(self, path, body, **kw):
        raise AssertionError(f"status wrote to {path}")


def questions_comment(*texts, cid=7):
    html = questions.comment_html([{"id": f"Q{i}", "question": t, "owner": "PO"} for i, t in enumerate(texts, 1)])
    return {"id": cid, "createdBy": {"id": ME}, "createdDate": "2026-10-01T00:00:00Z", "text": html}


def ledger_text(sha, *, spec_dir="specs/001-x", qlines="- [x] Q1: what? → yes · owner: PO\n", gate="ok", review="ok", record="", header="",
                red="aaaaaaa", green="bbbbbbb", old="ccccccc"):
    blocks = ""
    if gate:
        blocks += (f"### Gate — 2026-10-28 — `main...HEAD` @ `{old if gate == 'stale' else sha}`\n- traceability: ok\n"
                   f"- blocking: {'yes' if gate == 'blocking' else 'no'}\n\n")
    if review:
        blocks += review_md(old if review == "stale" else sha, blocks="yes" if review == "blocks" else "no")
    blocks += record
    return (f"# DoD — US{ID}: title\n\n- **Spec dir:** {spec_dir}\n{header}\n## 1. Seeded from ADO\n\n### Acceptance Criteria\nit works\n\n"
            f"{dod.SECTION2}\n- **Approach:** a\n- **Open questions → answers:**\n{qlines}\n"
            "## 3. AC → tests\n\n| AC | acceptance criterion | tests | commit/PR | status |\n|---|---|---|---|---|\n"
            f"| AC-1 | it works | tests/test_a.py::test_a | {green} | red {red} → green {green} |\n\n"
            f"## 4. Gate & review log\n{blocks}\n## 5. Definition of Done\n- [x] a\n")


def record_block(head, red, green, command):
    return (f"### Verification — 2026-10-30 @ {head[:12]}\n- AC-1: red {red[:12]} exit 1 · green {green[:12]} exit 0 · L1 ok\n"
            f"- head: {head[:12]} exit 0\n- command-sha256: {hashlib.sha256(command.encode()).hexdigest()[:12]}\n- seconds: 1.0\n\n")


@pytest.fixture
def env(tmp_path, monkeypatch, git_env):
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ado.stack.detect", lambda root: {"outcome": "unknown", "stacks": [], "domain_hints": []})
    return tmp_path


def world(repo, *, mode="advisory", cfg=None, questions="answered", plan=True, tasks="ticked", spec=True, decisions_md=None, gate="ok", review="ok",
          record=False, merged=True, ledger=True, header="", stack_profile="approved", early_ledger=False):
    """A complete story by default (every stage met); each keyword breaks one thing. Returns the Config."""
    git(repo, "init", "-q", "-b", "main")
    init = commit(repo, "chore: init", {"README.md": "x\n"})
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    if early_ledger:  # the ledger was pulled on the default branch before the work started
        commit(repo, f"docs: pull AB#{ID}", {f"dod/DoD_{ID}.md": ledger_text("abc1234", gate=None, review=None)})
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "-b", "feat/ab1001-x")
    command = COMMAND
    data = {"organization": "contoso", "project": "Fabrikam", **({"mode": "strict", "strict": {"test_command": command}} if mode == "strict" else {}), **(cfg or {})}
    files = {"ado.config.json": json.dumps(data)}
    if spec:
        files["specs/001-x/spec.md"] = "# spec\n"
        if plan:
            files["specs/001-x/plan.md"] = "# plan\n"
        if tasks:
            files["specs/001-x/tasks.md"] = TASKS[tasks]
    if decisions_md is not None:
        files[f"dod/Decisions_{ID}.md"] = decisions_md
    if mode == "strict" and stack_profile != "none":
        files["ado.stack.json"] = json.dumps(approve(profile()) if stack_profile == "approved" else profile())
    red = commit(repo, "test: red AB#1001", {"tests/test_a.py": "def test_a():\n    assert 0\n", **files})
    green = commit(repo, "feat: green AB#1001", {"src/app.py": "x = 1\n"})
    head = git(repo, "rev-parse", "HEAD")
    if ledger:
        rec = record_block(head, git(repo, "rev-parse", red), git(repo, "rev-parse", green), command) if record else ""
        qlines = {"answered": "- [x] Q1: what? → yes · owner: PO\n", "open": "- [ ] Q1: what? → _(open)_ · owner: PO\n", "none": ""}[questions]
        commit(repo, f"docs: ledger AB#{ID}", {f"dod/DoD_{ID}.md": ledger_text(green, qlines=qlines, gate=gate, review=review, record=rec, header=header,
                                                                              red=red, green=green, old=init)})
    if merged:
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return Config.load(str(repo))


def check(repo, board=None, stage=None, why=None, nxt=None, **kw):
    try:
        cfg = Config.load(str(repo))
    except ConfigError:
        cfg = None
    res = status.evaluate(board if board is not None else (Board(cfg) if cfg else None), ID, str(repo), **kw)
    if stage is not None:
        assert res["stage"] == stage, res
        if nxt or stage in NEXT:
            assert res["next"] == (nxt or NEXT[stage]), res
    if why is not None:
        assert why in res["why"], res
    return res


def test_a_complete_story_is_done(env):
    cfg = world(env)
    res = check(env, Board(cfg), "done")
    assert res["next"] == "none" and res["board_checked"] is True and res["run"] is None and res["not_checked"] == []


def test_stages_are_data_in_the_documented_order():
    ids = [s[0] for s in status.STAGES]
    assert ids == ["config", "ledger", "stack-profile", "rules", "questions-posted", "questions-answered", "plan", "tasks", "board-tasks",
                   "tasks-ticked", "board-sync", "decisions-posted", "verified", "gate", "review", "pr-link", "merged", "done-state",
                   "decisions-closed", "done"]
    assert all(len(s) == 4 and isinstance(s[1], bool) and callable(s[2]) and callable(s[3]) for s in status.STAGES)
    assert {s[0] for s in status.STAGES if s[1]} == {"questions-posted", "board-tasks", "board-sync", "decisions-posted", "pr-link", "done-state"}


# ---- one test per stage ----
def test_stage_config(env):
    check(env, None, "config", "no ado.config.json")
    assert check(env)["next"] == "ado setup"
    (env / "ado.config.json").write_text("{not json")
    res = check(env, None, "config", "invalid")
    assert res["next"] == "ado doctor"


def test_stage_ledger(env):
    world(env, ledger=False)
    check(env, None, "ledger")
    # a symlinked ledger is never read: fail closed
    (env / "dod").mkdir(exist_ok=True)
    (env / "outside.md").write_text(ledger_text("abc1234"))
    os.symlink(env / "outside.md", env / "dod" / f"DoD_{ID}.md")
    check(env, None, "ledger")


def test_stage_stack_profile(env):
    cfg = world(env, mode="strict", stack_profile="none")
    check(env, Board(cfg), "stack-profile")
    assert check(env)["next"] == "speckit.ado.stack"
    cfg = world_again(env, mode="strict", stack_profile="draft")
    assert check(env, Board(cfg), "stack-profile")["next"] == "ado stack approve"


def world_again(env, **kw):
    """A second world in the same directory: start over."""
    import shutil
    for child in env.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    return world(env, **kw)


def test_a_complete_strict_story_is_done(env):
    cfg = world(env, mode="strict", record=True)
    check(env, Board(cfg), "done")


def test_stage_rules(env):
    cfg = world(env, cfg={"rules": {"enabled": True}})  # a ledger with no `Rules from:` line
    res = check(env, Board(cfg), "rules")
    assert res["next"] == f"ado dod {ID} --refresh"
    p = env / "dod" / f"DoD_{ID}.md"
    p.write_text(p.read_text().replace("- **Spec dir:** specs/001-x\n", "- **Spec dir:** specs/001-x\n- **Rules from:** 900\n"))
    assert check(env, Board(cfg), "rules")["next"] == f"ado rules {ID}"  # the file Rules_900.md is not there
    (env / "dod" / "Rules_900.md").write_text(RULES_FILE)
    assert check(env, Board(cfg), "rules")["next"] == f"ado rules {ID} --confirm"  # first confirm: no hash recorded yet
    p.write_text(p.read_text().replace("- **Rules from:** 900\n", f"- **Rules from:** 900\n- **Rules reviewed:** BR-900-1@{RULE_HASH}\n"))
    assert check(env, Board(cfg))["stage"] != "rules"
    p.write_text(p.read_text().replace(RULE_HASH, "deadbeef"))
    res = check(env, Board(cfg), "rules")  # a recorded hash that is not the rule's: a human re-confirms
    assert res["next"] == f"ado rules {ID} --confirm" and res["why"] == "1 rule(s) differ from the hash recorded in `Rules reviewed:`: a human re-confirms them in their own terminal"


def test_rules_unconfirmed_is_free_changed_is_a_humans(env):
    cfg = world(env, cfg={"rules": {"enabled": True}}, header="- **Rules from:** 900\n")
    (env / "dod" / "Rules_900.md").write_text(RULES_FILE)
    res = check(env, Board(cfg), "rules", nxt=f"ado rules {ID} --confirm")
    assert res["why"] == "1 in-scope rule(s) are not confirmed yet: run the confirm yourself, no terminal is needed"


def test_rules_stage_only_when_enabled(env):
    cfg = world(env)  # rules off: no Rules lines, and the stage is not even looked at
    assert check(env, Board(cfg), "done")["stage"] == "done"
    assert "rules" not in status.applicable(cfg)
    assert "rules" in status.applicable(Config(organization="contoso", project="Fabrikam", rules={"enabled": True}))


def test_strict_only_stages_skipped_in_advisory(env):
    cfg = world(env, gate="ok")  # advisory: no profile, no verification record, open decision lines
    (env / "dod" / f"Decisions_{ID}.md").write_text("- [x] D1: a → rejected · by: PO · note: no\n")
    assert check(env, Board(cfg), "done")["stage"] == "done"
    ids = status.applicable(cfg)
    assert not {"stack-profile", "verified", "decisions-closed"} & set(ids)
    strict_ids = status.applicable(Config(organization="contoso", project="Fabrikam", mode="strict"))
    assert {"stack-profile", "verified", "decisions-closed"} <= set(strict_ids)


def test_stage_questions_posted(env):
    cfg = world(env, questions="open")
    check(env, Board(cfg), "questions-posted")
    # a posted comment that no longer matches section 2 is out of date too
    check(env, Board(cfg, comments=[questions_comment("something else")]), "questions-posted")


def test_stage_questions_answered(env):
    cfg = world(env, questions="open")
    res = check(env, Board(cfg, comments=[questions_comment("what?")]), "questions-answered", "1 open question")
    assert res["board_checked"] is True


def test_stage_plan(env):
    cfg = world(env, plan=False)
    check(env, Board(cfg), "plan")


def test_stage_tasks(env):
    cfg = world(env, tasks=None)
    check(env, Board(cfg), "tasks")
    (env / "specs/001-x/tasks.md").write_text("no tasks here\n")
    check(env, Board(cfg), "tasks")
    (env / "specs/001-x/tasks.md").unlink()  # a symlinked tasks.md is never read
    (env / "elsewhere.md").write_text(TASKS["ticked"])
    os.symlink(env / "elsewhere.md", env / "specs/001-x/tasks.md")
    check(env, Board(cfg), "tasks")


def test_stage_board_tasks(env):
    cfg = world(env)
    check(env, Board(cfg, children=[]), "board-tasks", "1 task")
    (env / "specs/001-x/tasks.md").write_text("- [x] T001 a\n- [x] T001 b\n")
    check(env, Board(cfg), "board-tasks", "repeats")


def test_stage_tasks_ticked(env):
    cfg = world(env, tasks="open")
    check(env, Board(cfg, children=[{**CHILD, "state": "New"}]), "tasks-ticked", "1 unticked")


def test_stage_board_sync(env):
    cfg = world(env)  # ticked in tasks.md, still New on the board
    check(env, Board(cfg, children=[{**CHILD, "state": "New"}]), "board-sync", "1 task")


DECISION = "- [ ] D1: use sqlite → _(open)_ · kind: decision · why: simple"
POSTED = f"{DECISION} · posted: 9:{decisions.line_hash('use sqlite')}\n"


def decisions_comment(cid=9):
    return {"id": cid, "createdBy": {"id": ME}, "createdDate": "2026-10-02T00:00:00Z", "text": f"{decisions.MARKER}<br>D1 (decision): use sqlite"}


def test_stage_decisions_posted(env):
    cfg = world(env, decisions_md=DECISION + "\n")
    check(env, Board(cfg), "decisions-posted", "1 decision")
    # a marker the file carries must match a decisions comment that is really on the story
    (env / "dod" / f"Decisions_{ID}.md").write_text(POSTED)
    check(env, Board(cfg), "decisions-posted", "not on the story")
    check(env, Board(cfg, comments=[{"id": 9, "createdBy": {"id": ME}, "createdDate": "2026-10-02T00:00:00Z", "text": "an unrelated comment"}]), "decisions-posted", "not on the story")
    assert check(env, Board(cfg, comments=[decisions_comment()]))["stage"] != "decisions-posted"


def test_stage_verified(env):
    cfg = world(env, mode="strict", record=False)
    res = check(env, Board(cfg), "verified", "no verification record")
    assert res["next"] == f"ado strict verify {ID}"


def test_stage_verified_stale(env):
    cfg = world(env, mode="strict", record=True, merged=False)
    commit(env, "feat: more AB#1001", {"src/more.py": "y = 2\n"})  # code after the record
    check(env, Board(cfg), "verified", "stale")


def test_stage_gate(env):
    cfg = world(env, gate=None)
    check(env, Board(cfg), "gate", "no gate result")


def test_stage_gate_stale(env):
    cfg = world(env, gate="stale", merged=False)
    check(env, Board(cfg), "gate", "stale")


def test_stage_gate_blocking(env):
    cfg = world(env, gate="blocking")
    check(env, Board(cfg), "gate", "blocking")


def test_stage_review_missing(env):
    cfg = world(env, review=None)
    check(env, Board(cfg), "review", "no review")


def test_stage_review_blocks(env):
    cfg = world(env, review="blocks")
    check(env, Board(cfg), "review", "blocks")


def test_stage_review_stale(env):
    cfg = world(env, review="stale", merged=False)
    check(env, Board(cfg), "review", "stale")


def test_stage_pr_link(env):
    cfg = world(env)
    check(env, Board(cfg, relations=[]), "pr-link")
    check(env, Board(cfg, relations=[BRANCH]), "pr-link")  # a branch link is not a PR


def test_pr_link_detected_from_relation(env):
    cfg = world(env)
    for rel in (AZURE_PR, GITHUB_PR, {**GITHUB_PR, "url": GITHUB_PR["url"].replace("%2f", "%2F")}):
        assert check(env, Board(cfg, relations=[BRANCH, GITHUB_COMMIT, rel]))["stage"] == "done"
    # a commit, a branch and look-alikes do not count
    url = GITHUB_PR["url"]
    for rel in (GITHUB_COMMIT, BRANCH, {**AZURE_PR, "rel": "Hyperlink"}, {"rel": "ArtifactLink"}, {**GITHUB_PR, "url": url + "x"}, {**GITHUB_PR, "url": url + "/1"},
                {**GITHUB_PR, "url": url.replace("12", "0")}, {**GITHUB_PR, "url": url.replace("12", "1a")}, {**GITHUB_PR, "url": " " + url},
                {**GITHUB_PR, "url": url.replace("PullRequest", "Commit")}, {**GITHUB_PR, "url": "https://github.com/contoso/web/pull/7"},
                {**AZURE_PR, "url": AZURE_PR["url"] + "\nvstfs:///x"}, {**AZURE_PR, "url": AZURE_PR["url"].replace(REPO, "repo-guid")},
                {**AZURE_PR, "url": AZURE_PR["url"] + "9z"}, {**AZURE_PR, "url": AZURE_PR["url"].replace("%2F7", "%2Fx")}):
        check(env, Board(cfg, relations=[rel]), "pr-link")


def test_stage_merged(env):
    cfg = world(env, merged=False)
    check(env, Board(cfg), "merged")
    git(env, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")  # nothing to compare with: not met, never guessed
    check(env, Board(cfg), "merged", "origin/HEAD", nxt="git remote set-head origin --auto")


def test_an_earlier_commit_naming_the_story_is_not_a_merge(env):
    # the ledger was pulled on main (a commit naming the story) and the PR is not merged: the default branch lacks the review/gate headings
    cfg = world(env, merged=False, early_ledger=True)
    assert "AB#1001" in git(env, "log", "--format=%s", "origin/main")
    check(env, Board(cfg), "merged")
    # a sibling commit on main naming a longer id changes nothing either
    git(env, "update-ref", "refs/remotes/origin/main", git(env, "commit-tree", "-m", "feat: other AB#10012", "-p", "origin/main", "origin/main^{tree}"))
    check(env, Board(cfg), "merged")


@pytest.mark.parametrize("how", ["ff", "merge", "squash"])
@pytest.mark.parametrize("mode", ["advisory", "strict"])
def test_merged_story_is_done_on_main_however_it_was_merged(env, how, mode):
    # after a merge the user checks out main; a squash or rebase leaves the recorded SHAs unreachable, which must not re-open gate, review, verified
    cfg = world(env, mode=mode, record=mode == "strict", merged=False)
    git(env, "checkout", "-q", "main")
    if how == "squash":
        git(env, "merge", "--squash", "feat/ab1001-x")
        git(env, "commit", "-q", "-m", "Add feature AB#1001 (#5)")
    else:
        git(env, "merge", "-q", *(["--ff-only"] if how == "ff" else ["--no-ff", "-m", "Merge pull request AB#1001"]), "feat/ab1001-x")
    git(env, "update-ref", "refs/remotes/origin/main", "HEAD")
    check(env, Board(cfg), "done")


def test_squash_merge_without_a_fetch_is_not_merged_yet(env):
    cfg = world(env, merged=False)
    git(env, "checkout", "-q", "main")
    git(env, "merge", "--squash", "feat/ab1001-x")
    git(env, "commit", "-q", "-m", "Add feature AB#1001 (#5)")  # origin/main still at the first commit
    res = check(env, Board(cfg))
    assert res["stage"] == "gate", res  # not on the default branch as far as git knows, so the old SHAs are judged as they are


def test_stage_done_state(env):
    cfg = world(env)
    check(env, Board(cfg, state="Active"), "done-state")


def test_stage_decisions_closed(env):
    cfg = world(env, mode="strict", record=True, decisions_md=POSTED)
    res = check(env, Board(cfg, comments=[decisions_comment()]), "decisions-closed")
    assert "1 decision" in res["why"]


def test_done_state_next_in_strict_points_at_open_decisions(env):
    # `ado done` refuses while a decision is open, so the next command is the one that closes them
    cfg = world(env, mode="strict", record=True, decisions_md=POSTED)
    check(env, Board(cfg, state="Active", comments=[decisions_comment()]), "done-state", nxt=f"ado decisions {ID}")


def test_stage_done(env):
    cfg = world(env)
    res = check(env, Board(cfg))
    assert res["stage"] == "done" and res["next"] == "none"


# ---- offline, network errors, no writes, no ADO text ----
def test_offline_skips_board_stages_says_not_checked(env):
    cfg = world(env, questions="open")
    board = Board(cfg)
    res = check(env, board, "questions-answered", offline=True)  # the local stage after the skipped questions-posted
    assert board.calls == [] and res["board_checked"] is False and res["not_checked"][0] == "questions-posted"


def test_offline_with_everything_local_met_is_never_done(env):
    cfg = world(env)
    board = Board(cfg)
    res = status.evaluate(board, ID, str(env), offline=True)
    assert res["stage"] == "board-not-checked" and res["board_checked"] is False and board.calls == []
    assert res["next"] == f"ado status {ID}" and set(res["not_checked"]) == {"questions-posted", "board-tasks", "board-sync", "decisions-posted", "pr-link", "done-state"}


@pytest.mark.parametrize("fail", [AdoError(f"HTTP 500 {UNTRUSTED}", 500, "http"), AdoError("down", None, "network"), ValueError("x")])
def test_network_error_never_reports_board_met(env, fail):
    cfg = world(env)
    board = Board(cfg, fail=fail)
    res = status.evaluate(board, ID, str(env))
    assert res["stage"] == "board-not-checked" and res["board_checked"] is False and "questions-posted" in res["not_checked"]
    assert res["next"] == "ado doctor" and len(board.calls) == 1  # one failure ends the reads; the other board stages are not tried
    assert UNTRUSTED not in json.dumps(res)


def test_network_error_still_reports_an_earlier_local_stage(env):
    cfg = world(env, plan=False, questions="none")
    res = status.evaluate(Board(cfg, fail=AdoError("down", None, "network")), ID, str(env))
    assert res["stage"] == "plan" and res["board_checked"] is False and res["not_checked"] == ["questions-posted"]


def test_no_client_is_not_checked(env):
    world(env)
    res = status.evaluate(None, ID, str(env))
    assert res["stage"] == "board-not-checked"


def test_status_never_writes(env):
    cfg = world(env, questions="open", tasks="open")
    board = Board(cfg, children=[{**CHILD, "state": "New"}])
    for kw in ({}, {"offline": True}):
        status.evaluate(board, ID, str(env), **kw)
    assert {m for m, _ in board.calls} <= {"GET", "POST"} and all(p.split("?")[0].endswith("/wit/wiql") for m, p in board.calls if m == "POST")


def test_no_ado_text_in_output(env, capsys, main_with):
    # story title, comment text and child titles carry a marker; so do the PR-controlled question and the failing request's message
    cfg = world(env, questions="open")
    p = env / "dod" / f"DoD_{ID}.md"
    p.write_text(p.read_text().replace("what?", f"{UNTRUSTED}-question?"))
    bodies = [questions_comment(f"{UNTRUSTED}-question?")]
    bodies[0]["text"] = bodies[0]["text"] + UNTRUSTED
    for board in (Board(cfg, comments=bodies, children=[{**CHILD, "title": f"[sk:T001] {UNTRUSTED}"}]), Board(cfg, fail=AdoError(UNTRUSTED, 500, "http"))):
        for flags in ([], ["--json"], ["--offline"]):
            main_with(board, flags)
            cap = capsys.readouterr()
            assert UNTRUSTED not in cap.out + cap.err, flags


# ---- the verb ----
@pytest.fixture
def main_with(monkeypatch):
    def run(board, flags):
        monkeypatch.setattr("ado.__main__.AdoClient", lambda cfg: board)
        return main(["status", str(ID), *flags])
    return run


def test_verb_prints_the_one_line_answer(env, capsys, main_with):
    cfg = world(env, plan=False)
    assert main_with(Board(cfg), []) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"Story {ID}: plan. Next: speckit.ado.plan {ID}"


def test_verb_json(env, capsys, main_with):
    cfg = world(env, plan=False)
    assert main_with(Board(cfg), ["--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["stage"] == "plan" and res["next"] == f"speckit.ado.plan {ID}" and res["board_checked"] is True and res["run"] is None
    assert set(res) == {"stage", "next", "why", "board_checked", "run", "not_checked", "board"}


def test_verb_offline_builds_no_client_and_prints_what_was_not_checked(env, capsys, monkeypatch):
    world(env, plan=False, questions="open")
    monkeypatch.setattr("ado.__main__.AdoClient", lambda cfg: pytest.fail("a client was built"))
    assert main(["status", str(ID), "--offline"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"Story {ID}: questions-answered. Next: speckit.ado.plan {ID}\n") and "Board not checked (offline): questions-posted" in out


def test_verb_without_a_config_says_setup(env, capsys):
    assert main(["status", str(ID)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"Story {ID}: config. Next: ado setup"


def test_verb_all_met_text(env, capsys, main_with):
    cfg = world(env)
    assert main_with(Board(cfg), []) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"Story {ID}: done. Next: none"


def test_verb_board_not_checked_text(env, capsys, main_with):
    cfg = world(env)
    assert main_with(Board(cfg, fail=AdoError("x", None, "network")), []) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"Story {ID}: board not checked. Next: ado doctor"


@pytest.mark.parametrize("bad", ["0", "-3", "abc", "2147483648", "1.5"])
def test_verb_rejects_a_bad_id(env, capsys, bad):
    with pytest.raises(SystemExit) as e:
        main(["status", bad])
    assert e.value.code == 2 and "error" in capsys.readouterr().err


def test_verb_accepts_catch_up_and_it_changes_nothing_yet(env, capsys, main_with):
    cfg = world(env, plan=False)
    assert main_with(Board(cfg), []) == 0
    plain = capsys.readouterr().out
    assert main_with(Board(cfg), ["--catch-up"]) == 0
    assert capsys.readouterr().out == plain



def test_board_checked_is_false_offline_even_for_a_local_stage(env):
    world(env, ledger=False)
    res = check(env, None, "ledger", offline=True)
    assert res["board"] == "offline" and res["board_checked"] is False
    res = check(env, Board(Config.load(str(env))), "ledger")
    assert res["board"] is None and res["board_checked"] is True


def test_unclosed_fence_next_is_a_refresh_only_when_it_can_repair(env):
    cfg = world(env)
    p = env / "dod" / f"DoD_{ID}.md"
    good = p.read_text()
    p.write_text(good.replace("it works\n", "```\nit works\n", 1))  # opened in section 1: `ado dod --refresh` splices from section 2
    check(env, Board(cfg), "ledger", "section 1", nxt=f"ado dod {ID} --refresh")
    p.write_text(good + "\n```\n")  # opened after section 2: a refresh refuses, so it is by hand
    res = check(env, Board(cfg), "ledger", "by hand", nxt=f"ado status {ID}")
