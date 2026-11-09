import json
import os
import subprocess
from pathlib import Path

import pytest

from ado import dod, prs
from ado.config import Config, Trust
from tests.conftest import FakeClient, commit, git, ledger_md, review_md


def test_azure_create_payload(cfg):
    c = FakeClient(cfg, [{"pullRequestId": 7}])
    out = prs.azure_create(c, "web app", "feat/x", "refs/heads/main", "T AB#1001", "body", [1001])
    call = c.calls[0]
    assert out == {"pullRequestId": 7}
    assert call["method"] == "POST"
    assert call["path"] == (f"/Fabrikam/_apis/git/repositories/web%20app/pullrequests"
                            f"?api-version={cfg.api_version}")
    assert call["body"] == {"sourceRefName": "refs/heads/feat/x", "targetRefName": "refs/heads/main",
                            "title": "T AB#1001", "description": "body",
                            "workItemRefs": [{"id": "1001"}]}


def test_azure_comment_payload(cfg):
    c = FakeClient(cfg)
    prs.azure_comment(c, "web", 7, "hello")
    call = c.calls[0]
    assert call["path"] == f"/Fabrikam/_apis/git/repositories/web/pullRequests/7/threads?api-version={cfg.api_version}"
    assert call["body"] == {"comments": [{"parentCommentId": 0, "content": "hello", "commentType": 1}],
                            "status": 1}


def test_github_args_list_form():
    assert prs.github_create_args("T", "b.md", "main", "feat") == [
        "gh", "pr", "create", "--title", "T", "--body-file", "b.md", "--base", "main", "--head", "feat"]


def test_title_gets_ab_ref():
    assert prs.with_ab_ref("Add x", 1001) == "Add x AB#1001"
    assert prs.with_ab_ref("Add AB#1001 x", 1001) == "Add AB#1001 x"
    assert prs.with_ab_ref("Add AB#10011", 1001) == "Add AB#10011 AB#1001"
    assert prs.with_ab_ref("(AB#1001)", 1001) == "(AB#1001)"


def test_render_plan_single_line_cells():
    out = prs.render_plan("azure", "T|x\nAB#1001", "line1\x1b[2J\nline2 | y", "main", "feat", [1001])
    table, _, sent = out.partition("## PR body (exactly as it will be sent)")
    assert "T\\|x AB#1001" in table and "\x1b" not in table
    assert all("line2" not in l or l.startswith("|") for l in table.splitlines())
    assert sent == "\n\nline1\x1b[2J\nline2 | y"


def _env(tmp_path, monkeypatch, host="github", repository="", responses=(), real_head=False):
    """`real_head=True` keeps the real head-commit lookup (it runs git); the other tests forbid or fake every subprocess, so they get none."""
    from ado import __main__ as m
    (tmp_path / "ado.config.json").write_text(json.dumps(
        {"organization": "contoso", "project": "Fabrikam", "repo_host": host, "repository": repository}))
    (tmp_path / "body.md").write_text("Body text\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    fake = {}

    def make(cfg):
        fake["c"] = FakeClient(cfg, responses=list(responses))
        return fake["c"]
    monkeypatch.setattr(m, "AdoClient", make)
    monkeypatch.setattr(m, "trusted", lambda repo=".", base=None: Trust([], "terminal", {}))  # these tests forbid or fake every subprocess; the trust read runs git
    monkeypatch.setattr(m, "_pr_trust", lambda base: Trust([], "terminal", {}))  # (`pr create` also resolves --base with git)
    if not real_head:
        monkeypatch.setattr(m, "_head_commit", lambda head: "")
    return m, fake


ARGS = ["pr", "create", "1001", "--title", "Add x", "--body-file", "body.md", "--base", "main", "--head", "feat"]


def _no_subprocess(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess.run must not be called")
    monkeypatch.setattr(subprocess, "run", boom)


@pytest.mark.parametrize("host,repo", [("github", ""), ("azure", "web")])
def test_open_dry_run_no_writes_no_subprocess(tmp_path, monkeypatch, capsys, host, repo):
    m, fake = _env(tmp_path, monkeypatch, host, repo)
    _no_subprocess(monkeypatch)
    assert m.main(ARGS + ["--plan-out", "out/plan.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and out["title"] == "Add x AB#1001"
    assert fake["c"].calls == []
    assert "AB#1001" in (tmp_path / "out" / "plan.md").read_text()


def test_open_apply_github_calls_gh(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch)
    seen = {}

    def run(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(args, 0, stdout="https://github.com/contoso/x/pull/5\n", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    assert m.main(ARGS + ["--apply"]) == 0
    assert seen["args"] == prs.github_create_args("Add x AB#1001", "-", "main", "feat")  # the body goes on stdin, the file is not read again
    assert seen["kw"] == {"input": "Body text\n", "check": False, "capture_output": True, "text": True, "encoding": "utf-8"}
    assert json.loads(capsys.readouterr().out)["url"] == "https://github.com/contoso/x/pull/5"
    assert fake["c"].calls == []


def test_open_apply_github_failure_and_missing_gh(tmp_path, monkeypatch, capsys):
    m, _ = _env(tmp_path, monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda a, **k: subprocess.CompletedProcess(a, 1, "", "nope"))
    assert m.main(ARGS + ["--apply"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "nope"}

    def missing(*a, **k):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(subprocess, "run", missing)
    assert m.main(ARGS + ["--apply"]) == 2
    assert "gh" in capsys.readouterr().err


def test_open_apply_azure_posts(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web", [{"pullRequestId": 9}])
    _no_subprocess(monkeypatch)
    assert m.main(ARGS + ["--apply"]) == 0
    body = fake["c"].calls[0]["body"]
    assert body["description"] == "Body text\n" and body["title"] == "Add x AB#1001"
    assert body["workItemRefs"] == [{"id": "1001"}]
    assert json.loads(capsys.readouterr().out)["pullRequestId"] == 9


def test_open_errors_exit_2(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, "azure", "")
    assert m.main(ARGS + ["--apply"]) == 2  # no repository configured
    assert m.main(["pr", "create", "1001", "--title", "t", "--body-file", "nope.md",
                   "--base", "a", "--head", "b"]) == 2
    assert fake["c"].calls == []


def test_comment_azure_and_github(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web")
    assert m.main(["pr", "comment", "7", "--body-file", "body.md"]) == 0
    assert fake["c"].calls[0]["body"]["comments"][0]["content"] == "Body text\n"
    m, fake = _env(tmp_path, monkeypatch, "github")
    assert m.main(["pr", "comment", "7", "--body-file", "body.md"]) == 2
    assert "gh pr comment" in capsys.readouterr().err
    assert fake["c"].calls == []


def _prepared(tmp_path, args=None):
    (tmp_path / ".ado").mkdir()
    (tmp_path / ".ado" / "pr-args.json").write_text(json.dumps(
        args or {"title": "Add x", "base": "main", "head": "feat"}))
    (tmp_path / ".ado" / "pr-body.md").write_text("Prepared body\n", encoding="utf-8")


def test_open_from_prepared(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web", [{"pullRequestId": 9}])
    _prepared(tmp_path)
    _no_subprocess(monkeypatch)
    assert m.main(["pr", "create", "1001", "--from-prepared", "--plan-out", ".ado/plan-pr.md"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is False and fake["c"].calls == []
    plan = (tmp_path / ".ado" / "plan-pr.md").read_text()
    assert "Add x AB#1001" in plan and "Prepared body" in plan
    assert m.main(["pr", "create", "1001", "--from-prepared", "--apply"]) == 0
    body = fake["c"].calls[0]["body"]
    assert body["title"] == "Add x AB#1001" and body["description"] == "Prepared body\n"
    assert body["sourceRefName"] == "refs/heads/feat" and body["targetRefName"] == "refs/heads/main"


def test_from_prepared_errors_exit_2(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web")
    assert m.main(["pr", "create", "1001", "--from-prepared"]) == 2  # nothing prepared
    assert ".ado/pr-args.json" in capsys.readouterr().err
    _prepared(tmp_path, {"title": "t", "base": "main"})  # no head
    assert m.main(["pr", "create", "1001", "--from-prepared"]) == 2
    _prepared_fix = tmp_path / ".ado" / "pr-args.json"
    _prepared_fix.write_text(json.dumps({"title": "t", "base": "main", "head": "-x; rm"}))
    assert m.main(["pr", "create", "1001", "--from-prepared"]) == 2  # option-like head refused
    for extra in (["--title", "t"], ["--body-file", "body.md"], ["--base", "a"], ["--head", "b"]):
        assert m.main(["pr", "create", "1001", "--from-prepared"] + extra) == 2
    assert m.main(["pr", "create", "1001"]) == 2  # explicit flags required without --from-prepared
    assert m.main(["pr", "create", "1001", "--push", "--title", "t", "--body-file", "body.md",
                   "--base", "a", "--head", "b"]) == 2  # --push only with --from-prepared
    assert fake["c"].calls == []


def test_push_is_list_form_and_only_with_apply(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, "github")
    _prepared(tmp_path)
    seen = []

    def run(args, **kw):
        seen.append((args, kw))
        return subprocess.CompletedProcess(args, 0, stdout="https://github.com/contoso/x/pull/5\n", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    assert m.main(["pr", "create", "1001", "--from-prepared", "--push"]) == 0  # dry run: no push
    assert seen == []
    assert m.main(["pr", "create", "1001", "--from-prepared", "--push", "--apply"]) == 0
    assert seen[0][0] == ["git", "push", "-u", "origin", "feat"] and seen[0][1].get("shell") is None
    assert seen[1][0] == prs.github_create_args("Add x AB#1001", "-", "main", "feat")
    assert seen[1][1]["input"] == "Prepared body\n"
    # a failed push stops before the PR is created
    seen.clear()
    capsys.readouterr()
    monkeypatch.setattr(subprocess, "run", lambda a, **k: (seen.append(a), subprocess.CompletedProcess(a, 1, "", "denied"))[1])
    assert m.main(["pr", "create", "1001", "--from-prepared", "--push", "--apply"]) == 1
    assert len(seen) == 1 and json.loads(capsys.readouterr().out)["error"] == "denied"


def test_plan_out_contains_full_body(tmp_path, monkeypatch):
    m, _ = _env(tmp_path, monkeypatch)
    long_body = "".join(f"line {i} of the body\n" for i in range(40))
    assert len(long_body) > 120
    (tmp_path / "body.md").write_text(long_body, encoding="utf-8")
    assert m.main(ARGS + ["--plan-out", "p1.md"]) == 0
    plan = (tmp_path / "p1.md").read_text()
    assert "## PR body (exactly as it will be sent)" in plan and plan.endswith(long_body)
    _prepared(tmp_path)
    (tmp_path / ".ado" / "pr-body.md").write_text(long_body, encoding="utf-8")
    assert m.main(["pr", "create", "1001", "--from-prepared", "--plan-out", "p2.md"]) == 0
    assert (tmp_path / "p2.md").read_text().endswith(long_body)


@pytest.mark.parametrize("bad", ["+main", ":main", "a:b", "--force", "x..y", "a//b", "a/", "a.lock", "a@{u}", "a b", ""])
def test_branch_names_strict(tmp_path, monkeypatch, capsys, bad):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web")
    _no_subprocess(monkeypatch)
    (tmp_path / ".ado").mkdir()
    for key in ("head", "base"):
        (tmp_path / ".ado" / "pr-args.json").write_text(json.dumps({"title": "t", "base": "main", "head": "feat", key: bad}))
        assert m.main(["pr", "create", "1001", "--from-prepared", "--push", "--apply"]) == 2
    assert m.main(["pr", "create", "1001", "--title", "t", "--body-file", "body.md", "--base", "main",
                   f"--head={bad}", "--apply"]) == 2
    assert fake["c"].calls == []


def test_branch_name_feature_accepted(tmp_path, monkeypatch):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web", [{"pullRequestId": 1}])
    _prepared(tmp_path, {"title": "t", "base": "release/1.0", "head": "feature/1001-login"})
    assert m.main(["pr", "create", "1001", "--from-prepared", "--apply"]) == 0


def test_azure_description_capped_at_4000(cfg):
    c = FakeClient(cfg, [{}])
    prs.azure_create(c, "web", "feat/x", "main", "T", "x" * 5000, [1001])
    d = c.calls[0]["body"]["description"]
    assert len(d) <= 4000
    assert d.endswith("\n… (truncated; full text in the ledger)")
    assert d.startswith("x" * 100)


def test_azure_description_at_limit_untouched(cfg):
    c = FakeClient(cfg, [{}])
    prs.azure_create(c, "web", "feat/x", "main", "T", "x" * 4000, [1001])
    assert c.calls[0]["body"]["description"] == "x" * 4000


def test_github_body_not_capped(tmp_path, monkeypatch, capsys):
    import ado.__main__ as m
    (tmp_path / "ado.config.json").write_text(
        '{"organization": "contoso", "project": "Fabrikam"}')
    (tmp_path / "b.md").write_text("y" * 5000)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setenv("ADO_TOKEN", "t")
    assert m.main(["pr", "create", "1001", "--title", "T", "--body-file", "b.md",
                   "--base", "main", "--head", "feat/x", "--plan-out", "plan.md"]) == 0
    assert "y" * 5000 in (tmp_path / "plan.md").read_text()


def test_azure_plan_shows_truncated_description(tmp_path, monkeypatch, capsys):
    import ado.__main__ as m
    (tmp_path / "ado.config.json").write_text(
        '{"organization": "contoso", "project": "Fabrikam", "repo_host": "azure", "repository": "web"}')
    (tmp_path / "b.md").write_text("y" * 5000)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setenv("ADO_TOKEN", "t")
    assert m.main(["pr", "create", "1001", "--title", "T", "--body-file", "b.md",
                   "--base", "main", "--head", "feat/x", "--plan-out", "plan.md"]) == 0
    plan = (tmp_path / "plan.md").read_text()
    assert "(truncated; full text in the ledger)" in plan
    assert "y" * 5000 not in plan


# ---- F24: unfinished work never reaches a normal PR ----
def _spec(tmp_path, tasks_md):
    (tmp_path / "dod").mkdir(exist_ok=True)
    (tmp_path / "dod" / "DoD_1001.md").write_text("- **Spec dir:** specs/001-x\n")
    (tmp_path / "specs" / "001-x").mkdir(parents=True, exist_ok=True)
    (tmp_path / "specs" / "001-x" / "tasks.md").write_text(tasks_md)


OPEN_TASKS = "- [x] T001 Done\n- [ ] T002 Open\n- [ ] T003 Open too\n"


@pytest.mark.parametrize("host,repo", [("github", ""), ("azure", "web")])
@pytest.mark.parametrize("apply", [False, True])
def test_pr_refuses_unticked_tasks(tmp_path, monkeypatch, capsys, host, repo, apply):
    m, fake = _env(tmp_path, monkeypatch, host, repo)
    _spec(tmp_path, OPEN_TASKS)
    _no_subprocess(monkeypatch)
    assert m.main(ARGS + ["--plan-out", "out/plan.md"] + (["--apply"] if apply else [])) == 2
    err = capsys.readouterr().err
    assert "T002, T003" in err and "T001" not in err
    assert not (tmp_path / "out").exists() and fake["c"].calls == []


def test_pr_allow_incomplete_github_is_draft(tmp_path, monkeypatch, capsys):
    m, _ = _env(tmp_path, monkeypatch)
    _spec(tmp_path, OPEN_TASKS)
    assert m.main(ARGS + ["--allow-incomplete", "--plan-out", "plan.md"]) == 0
    assert "| draft | yes — 2 unticked task(s): T002, T003 |" in (tmp_path / "plan.md").read_text()
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda a, **k: seen.update(args=a) or subprocess.CompletedProcess(a, 0, "u\n", ""))
    assert m.main(ARGS + ["--allow-incomplete", "--apply"]) == 0
    assert "--draft" in seen["args"]


def test_pr_allow_incomplete_azure_is_draft(tmp_path, monkeypatch):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web", [{"pullRequestId": 9}])
    _spec(tmp_path, OPEN_TASKS)
    assert m.main(ARGS + ["--allow-incomplete", "--apply"]) == 0
    assert fake["c"].calls[0]["body"]["isDraft"] is True


def test_pr_all_ticked_is_not_draft(tmp_path, monkeypatch):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web", [{"pullRequestId": 9}])
    _spec(tmp_path, "- [x] T001 Done\n")
    assert m.main(ARGS + ["--apply"]) == 0
    assert "isDraft" not in fake["c"].calls[0]["body"]
    assert "--draft" not in prs.github_create_args("T", "b.md", "main", "feat")


def test_pr_no_tasks_md_proceeds_with_not_checked_line(tmp_path, monkeypatch):
    m, _ = _env(tmp_path, monkeypatch)
    assert m.main(ARGS + ["--plan-out", "plan.md"]) == 0  # no ledger at all
    assert "| tasks | not checked (no tasks.md) |" in (tmp_path / "plan.md").read_text()
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod" / "DoD_1001.md").write_text("- **Spec dir:** specs/missing\n")  # ledger, but no tasks.md
    assert m.main(ARGS + ["--plan-out", "plan2.md"]) == 0
    assert "not checked (no tasks.md)" in (tmp_path / "plan2.md").read_text()


def test_pr_tasks_md_without_task_lines_is_not_all_ticked(tmp_path, monkeypatch):
    m, _ = _env(tmp_path, monkeypatch)
    _spec(tmp_path, "# Tasks\n\nnothing parseable\n")
    assert m.main(ARGS + ["--plan-out", "plan.md"]) == 0
    plan = (tmp_path / "plan.md").read_text()
    assert "not checked (no tasks found in specs/001-x/tasks.md)" in plan and "all 0" not in plan


def test_pr_command_doc_stop_and_offer_rule():
    text = (Path(__file__).resolve().parent.parent / "commands" / "pr.md").read_text()
    for needle in ("After a CLI refusal (exit 2): **stop**", "explicit human yes", "both carry `--allow-incomplete`",
                   "as a draft?"):
        assert needle in text
    blocks = [b.split("```")[0] for b in text.split("```bash")[1:] if "ado pr create <id> --from-prepared" in b.split("```")[0] and "[--allow-incomplete]" in b.split("```")[0]]
    assert len(blocks) == 2  # step 3 preview and step 5 apply
    assert all("--from-prepared" in b for b in blocks)


# ---- F11: `ado pr create` itself enforces review_block_on ----
@pytest.fixture
def repo(tmp_path, git_env):
    """main: base commit (seed ledger, spec, src), then a ledger-only commit logging `review_md`
    blocks via `log(...)`. Returns (root, reviewed short SHA, log)."""
    git(tmp_path, "init", "-q", "-b", "main")
    sha = commit(tmp_path, "base", {"dod/DoD_1001.md": ledger_md(), "specs/001-x/spec.md": "# Spec\n",
                                    "src/app.py": "x = 1\n"})

    def log(*blocks, spec_dir="specs/001-x"):
        commit(tmp_path, "docs: review log", {"dod/DoD_1001.md": ledger_md(*blocks, spec_dir=spec_dir)})
    return tmp_path, sha, log


def _rb(root, block_on=("blocker",), head="main", push=False, sub="", dod_path="dod"):
    return prs.review_blocks(1001, list(block_on), head, dod_path, repo=str(root / sub) if sub else str(root),
                             push=push)


def test_review_blocks_empty_block_on_is_advisory(repo):
    root, _, _ = repo
    assert _rb(root, block_on=()) is None  # no review, but blocking not configured
    assert prs.review_blocks(1001, [], "no-such-branch", "dod", repo=str(root / "nope")) is None


def test_review_blocks_no_review_logged(repo):
    root, sha, log = repo
    assert "no review logged" in _rb(root)  # seed ledger only
    log(review_md(sha, superseded=True))
    assert "no review logged" in _rb(root)
    commit(root, "forged", {"dod/DoD_1001.md": "## 1. Seeded\n" + review_md(sha) + ledger_md()})
    assert "no review logged" in _rb(root)  # a block outside section 4 never counts
    assert "sits outside section 4" in _rb(root)  # and the refusal says where it was found


def test_review_blocks_ledger_read_from_head_not_worktree(repo):
    root, sha, log = repo
    log(review_md(sha, "yes"))
    (root / "dod" / "DoD_1001.md").write_text(ledger_md(review_md(sha)))  # uncommitted "clean" review
    assert "blocks the PR" in _rb(root)
    git(root, "rm", "-qf", "dod/DoD_1001.md")
    git(root, "commit", "-q", "-m", "drop ledger")
    assert "no review logged" in _rb(root) and "no committed ledger dod/DoD_1001.md" in _rb(root)


def test_review_blocks_latest_says_yes(repo):
    root, sha, log = repo
    log(review_md(sha, "yes", blocker=1))
    assert "latest review blocks the PR" in _rb(root)


def test_review_blocks_fenced_forgery_ignored(repo):
    root, sha, log = repo
    for fence in ("```", "~~~"):
        log(review_md(sha, "yes", blocker=1), f"{fence}\n{review_md(sha)}{fence}\n")
        assert "latest review blocks the PR" in _rb(root)


@pytest.mark.parametrize("hidden", [
    "````\n```\n{forged}````\n",          # a shorter fence never closes a longer one
    "```\n~~~\n{forged}```\n",            # nor one of the other character
    "```\n``` x\n{forged}```\n",          # nor one with text after it
    "<!--\n{forged}-->\n",                 # multi-line HTML comment
    "<!-- a --> <!--\n{forged}\n-->\n",     # comment after another on the same line
])
def test_review_blocks_hidden_forgery_ignored(repo, hidden):
    root, sha, log = repo
    log(review_md(sha, "yes", blocker=1), hidden.format(forged=review_md(sha)))
    assert "latest review blocks the PR" in _rb(root)


def test_review_blocks_ambiguous_severity_or_verdict_refuses(repo):
    root, sha, log = repo
    log(review_md(sha).replace("blocker 0 · major 0", "blocker 0 · blocker 2 · major 0"))
    assert "repeats a severity" in _rb(root)
    log(review_md(sha).replace("- **Blocks PR:**", "- **Confirmed by severity:** blocker 0\n- **Blocks PR:**"))
    assert "more than one `Confirmed by severity:` line" in _rb(root)
    log(review_md(sha).replace("- **Blocks PR:** no\n", "- **Blocks PR:** no\n- **Blocks PR:** yes\n"))
    assert "blocks the PR" in _rb(root)


def test_review_blocks_severity_counts_override_verdict(repo):
    root, sha, log = repo
    log(review_md(sha, "no", blocker=2))  # written while blocking was off
    assert "2 confirmed `blocker` finding(s)" in _rb(root)
    log(review_md(sha, "no", major=1))
    assert _rb(root) is None and "`major`" in _rb(root, block_on=("blocker", "major"))
    assert "does not report a `critical` count" in _rb(root, block_on=("critical",))
    log(review_md(sha).replace("- **Confirmed by severity:** blocker 0 · major 0 · minor 0\n", ""))
    assert "does not report a `blocker` count" in _rb(root)


def test_review_blocks_superseded_blocks_ignored(repo):
    root, sha, log = repo
    log(review_md(sha, "yes", superseded=True), review_md(sha, "no"))
    assert _rb(root) is None
    log(review_md(sha, "no", superseded=True), review_md(sha, "yes"))
    assert "blocks" in _rb(root)


def test_review_blocks_sha_not_ancestor_is_stale(repo):
    root, _, log = repo
    git(root, "checkout", "-q", "-b", "side")
    side = commit(root, "side work", {"src/side.py": "y = 2\n"})
    git(root, "checkout", "-q", "main")
    log(review_md(side))
    why = _rb(root)
    assert "stale" in why and side in why and "not an ancestor" in why
    log(review_md("deadbee"))
    assert "review SHA deadbee is ambiguous or not a commit" in _rb(root)  # unknown SHA


def test_review_blocks_later_code_change_is_stale(repo):
    root, sha, log = repo
    log(review_md(sha))
    commit(root, "code", {"src/app.py": "x = 2\n"})
    assert _rb(root) == (f"review is stale: review is for {sha}; later commits change 1 file(s) outside the "
                         "ledger/spec — re-run speckit.ado.review")


def test_review_blocks_rename_out_of_src_is_stale(repo):
    root, sha, log = repo
    log(review_md(sha))
    git(root, "mv", "src/app.py", "dod/app.py")
    git(root, "commit", "-q", "-m", "move")
    assert "stale" in _rb(root)  # the deletion of src/app.py counts


def test_review_blocks_ledger_and_spec_commits_keep_it_fresh(repo):
    root, sha, log = repo
    log(review_md(sha))
    commit(root, "docs: spec + ledger", {"specs/001-x/tasks.md": "- [x] T001 a\n"})
    assert _rb(root) is None


@pytest.fixture
def gate_review_2a(tmp_path, git_env):
    """The shipped order: gate commits the ledger (spec dir set, spec not committed), review logs the
    gate commit's SHA, then step 2a commits spec.md + tasks.md + ledger. Returns (root, reviewed SHA)."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "base", {"src/app.py": "x = 1\n", "tests/test_app.py": "def test(): pass\n"})
    sha = commit(tmp_path, "docs: gate log", {"dod/DoD_1001.md": ledger_md("### Gate — x\n")})
    commit(tmp_path, "docs: review log", {"dod/DoD_1001.md": ledger_md("### Gate — x\n", review_md(sha))})
    assert _rb(tmp_path) is None
    commit(tmp_path, "docs: spec + ledger AB#1001", {
        "specs/001-x/spec.md": "# Spec\n", "specs/001-x/tasks.md": "- [x] T001 a\n",
        "dod/DoD_1001.md": ledger_md("### Gate — x\n", review_md(sha), "### Gate — y\n")})
    return tmp_path, sha


def test_review_blocks_step_2a_spec_commit_keeps_it_fresh(gate_review_2a):
    root, _ = gate_review_2a
    assert _rb(root) is None


@pytest.mark.parametrize("path", ["src/app.py", "tests/test_app.py", "ado.config.json", ".github/workflows/ci.yml"])
def test_review_blocks_step_2a_then_other_file_is_stale(gate_review_2a, path):
    root, sha = gate_review_2a
    commit(root, "later", {path: "changed\n"})
    assert "stale" in _rb(root) and sha in _rb(root) and "1 file(s)" in _rb(root)


def test_review_blocks_spec_dir_new_after_review_must_be_new_and_regular(repo):
    root, _, log = repo
    # Spec dir pointed at code that exists at the review SHA: adding tasks.md there later never blesses src/
    sha = commit(root, "spec dir src", {"dod/DoD_1001.md": ledger_md(spec_dir="src")})
    log(review_md(sha), spec_dir="src")
    commit(root, "sneak", {"src/tasks.md": "- [x] T001 a\n", "src/app.py": "x = 9\n"})
    assert "stale" in _rb(root) and "2 file(s)" in _rb(root)
    # a spec dir first committed after the review counts only with a regular-file spec.md/tasks.md
    sha = commit(root, "spec dir new", {"dod/DoD_1001.md": ledger_md(spec_dir="specs/002-y")})
    log(review_md(sha), spec_dir="specs/002-y")
    (root / "specs" / "002-y").mkdir(parents=True)
    os.symlink("../../src/app.py", root / "specs" / "002-y" / "tasks.md")
    commit(root, "symlink spec", {"specs/002-y/notes.md": "n\n"})
    assert "stale" in _rb(root) and "2 file(s)" in _rb(root)


def test_review_blocks_from_a_subdirectory(repo):
    root, sha, log = repo
    log(review_md(sha))
    commit(root, "docs: gate log", {"dod/DoD_1001.md": ledger_md(review_md(sha), "### Gate — x\n")})
    # a cwd-relative dod_path maps onto repo-root paths (git show / git diff are root-relative)
    assert _rb(root, sub="src", dod_path="../dod") is None
    commit(root, "code", {"src/app.py": "x = 2\n"})
    assert "stale" in _rb(root, sub="src", dod_path="../dod")


def test_review_blocks_uses_head_branch_not_checkout(repo):
    root, sha, log = repo
    log(review_md(sha))
    git(root, "branch", "feat")
    commit(root, "code on main only", {"src/app.py": "x = 3\n"})
    assert _rb(root, head="feat") is None
    assert "stale" in _rb(root, head="main")


def test_review_blocks_tag_cannot_shadow_branch(repo):
    root, sha, log = repo
    log(review_md(sha))
    git(root, "tag", "main")  # tag `main` at the reviewed, fresh commit
    commit(root, "unreviewed", {"src/app.py": "x = 4\n"})
    assert "stale" in _rb(root)  # branch main is checked, not the tag


@pytest.mark.parametrize("kind", ["tag", "branch"])
def test_review_blocks_ref_named_like_review_sha_cannot_stand_in(repo, kind):
    root, sha, log = repo
    log(review_md(sha))
    commit(root, "unreviewed", {"src/app.py": "x = 5\n"})
    git(root, kind, sha)  # a ref named like the short SHA, at the unreviewed tip
    assert "stale" in _rb(root)


def test_review_blocks_review_sha_must_be_one_commit(repo, monkeypatch):
    root, sha, log = repo
    blob = git(root, "rev-parse", "--short=8", "HEAD:src/app.py")
    log(review_md(blob))
    assert f"review SHA {blob} is ambiguous or not a commit" in _rb(root)
    for bad in ("abc12", "not-a-sha", sha + "z"):
        log(review_md(bad))
        assert "is not 7-40 hex characters" in _rb(root)
    log(review_md(sha))
    real = prs._git

    def two_objects(repo_, *args):  # a 7-hex prefix collision can't be made cheaply in a test
        if args[0] == "rev-parse" and args[1].startswith("--disambiguate="):
            return subprocess.CompletedProcess(args, 0, b"a" * 40 + b"\n" + b"b" * 40 + b"\n", b"")
        return real(repo_, *args)
    monkeypatch.setattr(prs, "_git", two_objects)
    assert "ambiguous or not a commit" in _rb(root)


def test_review_blocks_fenced_fake_section_4_ignored(repo):
    root, sha, log = repo
    fake = "```\n## 4. Gate & review log\n\n" + review_md(sha) + "```\n\n"
    real = ledger_md(review_md(sha, "yes", blocker=1)).replace("## 3. AC\n\n", "## 3. AC\n\n" + fake)
    commit(root, "docs: review log", {"dod/DoD_1001.md": real})
    assert "latest review blocks the PR" in _rb(root)
    commit(root, "comment", {"dod/DoD_1001.md": real.replace(fake, fake.replace("```", "<!--", 1).replace("```", "-->"))})
    assert "latest review blocks the PR" in _rb(root)


def test_review_blocks_replace_ref_cannot_hide_a_change(repo):
    root, sha, log = repo
    log(review_md(sha))
    commit(root, "code", {"src/app.py": "x = 2\n"})
    git(root, "replace", "-f", git(root, "rev-parse", sha), git(root, "rev-parse", "main"))
    assert git(root, "diff", "--name-only", sha, "main") == ""  # plain git now sees no change
    assert "stale" in _rb(root)


def test_review_blocks_graft_cannot_fake_ancestry(repo):
    root, _, log = repo
    git(root, "checkout", "-q", "-b", "side")
    side = commit(root, "side", {"src/app.py": "x = 2\n"})
    git(root, "checkout", "-q", "main")
    log(review_md(side))
    commit(root, "same code", {"src/app.py": "x = 2\n"})  # tree matches side but for the ledger
    tip, parent, side_full = (git(root, "rev-parse", r) for r in ("main", "main~1", side))
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "grafts").write_text(f"{tip} {parent} {side_full}\n")
    assert subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", side, "main"],
                          capture_output=True).returncode == 0  # plain git is fooled
    assert "not an ancestor" in _rb(root)


UNCLOSED = ("cannot check the review: ledger contains an unclosed code fence or HTML comment (or one hiding a "
            "`## 2.` heading); re-pull §1 with `ado dod 1001 --refresh` (it escapes story text), or edit the ledger")


@pytest.mark.parametrize("opener", ["``` from ADO", "~~~", "<!-- from ADO"])
def test_review_blocks_unclosed_opener_in_section_1_is_named(repo, opener):
    root, sha, _ = repo
    story = ledger_md(review_md(sha)).replace("# DoD\n", f"# DoD\n{opener}\n")  # clean review otherwise
    commit(root, "docs: review log", {"dod/DoD_1001.md": story})
    assert _rb(root) == UNCLOSED


def test_review_blocks_fenced_section_2_marker_refused(repo):
    root, sha, _ = repo
    fake = f"```\n{dod.SECTION2}\n\n## 4. Gate & review log\n\n{review_md(sha)}```\n\n"
    real = ledger_md(review_md(sha, "yes", blocker=1)).replace("## 3. AC\n\n", "## 3. AC\n\n" + fake)
    commit(root, "docs: review log", {"dod/DoD_1001.md": real})
    assert _rb(root) == UNCLOSED
    commit(root, "comment", {"dod/DoD_1001.md": real.replace(fake, fake.replace("```", "<!--", 1).replace("```", "-->"))})
    assert _rb(root) == UNCLOSED


def test_review_blocks_head_alias_resolves_current_branch(repo):
    root, sha, log = repo
    log(review_md(sha))
    assert _rb(root, head="HEAD") is None
    git(root, "tag", "main")  # same-named tag: `symbolic-ref --short` would say heads/main
    assert _rb(root, head="HEAD") is None
    git(root, "update-ref", "refs/remotes/origin/main", sha)
    assert "remote branch origin/main differs" in _rb(root, head="HEAD")  # remote check applies to it
    git(root, "checkout", "-q", "--detach")
    assert "HEAD is detached" in _rb(root, head="HEAD")
    full = git(root, "rev-parse", "HEAD")
    assert "unknown head" in _rb(root, head=full)  # a SHA is not a branch: no special case


def test_review_blocks_remote_branch_must_match_unless_push(repo):
    root, sha, log = repo
    log(review_md(sha))
    git(root, "update-ref", "refs/remotes/origin/main", sha)  # remote still at the pre-review commit
    assert "remote branch origin/main differs" in _rb(root)
    assert _rb(root, push=True) is None  # --push sends the checked local branch
    git(root, "update-ref", "refs/remotes/origin/main", "main")
    assert _rb(root) is None


def test_review_blocks_invalid_spec_dir_is_ignored(repo):
    root, _, log = repo
    sha = commit(root, "spec dir .", {"dod/DoD_1001.md": ledger_md(spec_dir=".")})  # `.` would allow everything
    log(review_md(sha))
    commit(root, "docs: spec", {"specs/001-x/tasks.md": "- [x] T001 a\n"})
    assert "stale" in _rb(root)
    git(root, "rm", "-q", "specs/001-x/spec.md")
    sha = commit(root, "spec dir without spec.md/tasks.md")  # Spec dir: specs/001-x, but nothing in it
    git(root, "rm", "-q", "specs/001-x/tasks.md")
    sha = commit(root, "drop tasks", {"dod/DoD_1001.md": ledger_md(), "specs/001-x/notes.md": "n\n"})
    log(review_md(sha))
    commit(root, "spec edit", {"specs/001-x/notes.md": "n2\n"})
    assert "stale" in _rb(root)


def test_review_blocks_spec_dir_taken_from_review_commit(repo):
    root, sha, log = repo
    log(review_md(sha))
    # the bypass: one later commit repoints Spec dir at src/, adds src/tasks.md and changes code
    commit(root, "sneak", {"dod/DoD_1001.md": ledger_md(review_md(sha), spec_dir="src"),
                           "src/tasks.md": "- [x] T001 a\n", "src/app.py": "x = 9\n"})
    assert "stale" in _rb(root) and "2 file(s)" in _rb(root)


def test_review_blocks_no_ledger_at_review_commit_allows_only_dod(repo):
    root, _, log = repo
    git(root, "rm", "-q", "dod/DoD_1001.md")
    sha = commit(root, "no ledger yet")
    log(review_md(sha))  # ledger now exists, at a commit after the review SHA
    assert _rb(root) is None
    commit(root, "docs: spec", {"specs/001-x/tasks.md": "- [x] T001 a\n"})
    assert "stale" in _rb(root)


def test_review_blocks_refuses_without_git_or_head(repo, monkeypatch):
    root, sha, log = repo
    log(review_md(sha))
    assert "unknown head 'no-such-branch'" in _rb(root, head="no-such-branch")
    assert "unknown head" in _rb(root.parent / "not-a-repo-xyz")

    def missing(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", missing)
    assert "`git` is not installed" in _rb(root)


def test_review_blocks_undecodable_file_name_refuses(repo):
    root, sha, log = repo
    log(review_md(sha))
    blob = git(root, "hash-object", "-w", "--stdin")
    subprocess.run([b"git", b"-C", str(root).encode(), b"update-index", b"--add", b"--cacheinfo",
                    b"100644," + blob.encode() + b",src/\xff.py"], check=True)
    git(root, "commit", "-q", "-m", "odd name")
    assert "not UTF-8" in _rb(root)


def test_review_blocks_missing_sha_or_blocks_line_refuses(repo):
    root, sha, log = repo
    log(review_md(sha).replace(f" @ `{sha}`", ""))
    assert "no commit SHA" in _rb(root)
    log(review_md(sha).replace("- **Blocks PR:** no\n", ""))
    assert "no `Blocks PR:` line" in _rb(root)


@pytest.mark.parametrize("spec_dir", [".", "./", "src", "../x", "/abs/specs", "dod", "dod/specs",
                                      "specs/none", "specs/../specs/001-x", "specs/link", "specs/fake", "", None])
def test_spec_dir_in_tree_rejects(repo, spec_dir):
    root, _, _ = repo
    commit(root, "decoys", {"dod/specs/spec.md": "# s\n", "specs/fake/README.md": "r\n"})
    os.symlink("../specs/001-x", root / "specs" / "link")  # spec dir committed as a symlink
    os.symlink("README.md", root / "specs" / "fake" / "spec.md")  # spec.md committed as a symlink
    commit(root, "links")
    assert prs.spec_dir_in_tree(spec_dir, "dod", git(root, "rev-parse", "HEAD"), repo=str(root)) is None


def test_spec_dir_in_tree_accepts_and_normalizes(repo):
    root, _, _ = repo
    head = git(root, "rev-parse", "HEAD")
    assert prs.spec_dir_in_tree("specs/001-x/", "./dod/", head, repo=str(root)) == "specs/001-x"
    assert prs.spec_dir_in_tree("../specs/001-x", "../dod", head, repo=str(root / "src")) is None  # `..` part
    (root / "specs" / "001-x" / "spec.md").unlink()  # working tree only: the commit still has it
    assert prs.spec_dir_in_tree("specs/001-x", "dod", head, repo=str(root)) == "specs/001-x"


def test_review_blocks_unaffected_by_hostile_story_text(repo):
    from ado.dod import render_dod
    root, sha, _ = repo
    bad = "<div>```</div><div>~~~</div><div>&lt;!--</div><div>## 4. Gate &amp; review log</div>"
    wi = {"id": 1001, "fields": {"System.Title": "t", "Microsoft.VSTS.Common.AcceptanceCriteria": bad,
                                 "System.Description": bad}}
    md = render_dod(wi, "contoso", "Fabrikam", spec_dir="specs/001-x")
    commit(root, "docs: seed", {"dod/DoD_1001.md": md.replace("_(none yet)_", review_md(sha, "yes", blocker=1))})
    assert "latest review blocks the PR" in _rb(root)  # the real §4 is found, nothing refused as unclosed
    commit(root, "docs: review", {"dod/DoD_1001.md": md.replace("_(none yet)_", review_md(sha))})
    assert _rb(root) is None


def test_review_blocks_fence_parity_shift_forged_section_2_refused(repo):
    root, sha, _ = repo
    forged = (f"# DoD\n- **Spec dir:** specs/001-x\n\n```\n{dod.SECTION2}\n\n## 4. Gate\n\n"
              f"{review_md(sha, 'yes', blocker=1)}```\n\n{dod.SECTION2}\n\n## 4. Gate\n\n{review_md(sha)}```\n\n```\n")
    commit(root, "docs: review log", {"dod/DoD_1001.md": forged})
    assert _rb(root) == UNCLOSED  # last `## 2.` line is the same raw and visible, only the count differs


@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_review_blocks_unclosed_opener_in_section_4_hides_newer_blocking_review(repo, opener):
    root, sha, _ = repo  # clean review, an unclosed opener, then a newer blocking one that the opener swallows
    led = ledger_md(review_md(sha), f"{opener}\n", review_md(sha, "yes", blocker=1))
    commit(root, "docs: review log", {"dod/DoD_1001.md": led})
    assert _rb(root) == UNCLOSED  # not None (the stale clean review) and not "blocks the PR" (never seen)


def test_review_blocks_hidden_section_2_with_equal_count_but_moved_position_refused(repo):
    root, sha, _ = repo  # M101: one `## 2.` hidden in a fence, another created by a comment prefix: counts match
    led = (f"# DoD\n- **Spec dir:** specs/001-x\n\n{dod.SECTION2}\n\n```\n{dod.SECTION2}\n```\n\n"
           f"<!-- x -->{dod.SECTION2}\n\n## 3. AC\n\n## 4. Gate\n\n{review_md(sha)}## 5. Checklist\n- [ ] x\n")
    visible = prs._visible(led)[0]
    assert len(dod._SECTION2_RE.findall(led)) == len(dod._SECTION2_RE.findall(visible)) == 2
    assert prs._section2_line(led) != prs._section2_line(visible)
    commit(root, "docs: review log", {"dod/DoD_1001.md": led})
    assert _rb(root) == UNCLOSED


# ---- F22: --apply binds to the approved preview (--expect-plan) ----
CHANGED = "plan changed since approval — re-run the preview and approve again"


def test_pr_apply_refuses_when_plan_changed(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, "github")
    _prepared(tmp_path)
    _no_subprocess(monkeypatch)  # no git push, no gh pr create
    assert m.main(["pr", "create", "1001", "--from-prepared", "--push", "--plan-out", ".ado/plan-pr.md"]) == 0
    (tmp_path / ".ado" / "pr-body.md").write_text("Body edited after approval\n", encoding="utf-8")
    capsys.readouterr()
    assert m.main(["pr", "create", "1001", "--from-prepared", "--push", "--apply",
                   "--expect-plan", ".ado/plan-pr.md"]) == 2
    assert CHANGED in capsys.readouterr().err and fake["c"].calls == []
    assert "Body edited after approval" in (tmp_path / ".ado" / "plan-pr.md.new").read_text()


def test_pr_apply_proceeds_when_plan_matches(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web", [_created(9), {"value": [{"id": "1001"}]}])
    _prepared(tmp_path)
    assert m.main(["pr", "create", "1001", "--from-prepared", "--plan-out", ".ado/plan-pr.md"]) == 0
    assert m.main(["pr", "create", "1001", "--from-prepared", "--apply", "--expect-plan", ".ado/plan-pr.md"]) == 0
    assert [c["method"] for c in fake["c"].calls] == ["POST", "GET"]


def test_pr_apply_repairs_a_dropped_work_item_link(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web", [_created(9), {"value": []}, {}])
    _prepared(tmp_path)
    assert m.main(["pr", "create", "1001", "--from-prepared", "--apply"]) == 0
    assert [c["method"] for c in fake["c"].calls] == ["POST", "GET", "PATCH"]
    assert json.loads(capsys.readouterr().out)["work_items_linked"] == [1001]


def test_long_pr_plan_warns_first_line_and_keeps_full_text(tmp_path, monkeypatch):
    m, _ = _env(tmp_path, monkeypatch)
    long_body = "".join(f"line {i}\n" for i in range(250))
    (tmp_path / "body.md").write_text(long_body, encoding="utf-8")
    assert m.main(ARGS + ["--plan-out", "p.md"]) == 0
    plan = (tmp_path / "p.md").read_text()
    first = plan.splitlines()[0]
    assert first.startswith("⚠ Preview longer than the approval gate can show (")
    assert "open p.md and read it in full" in first and f"({len(plan.splitlines()) - 1} lines)" in first
    assert plan.endswith(long_body) and "Plan for pull request" in plan


def test_short_pr_plan_has_no_warning(tmp_path, monkeypatch):
    m, _ = _env(tmp_path, monkeypatch)
    assert m.main(ARGS + ["--plan-out", "p.md"]) == 0
    assert "⚠" not in (tmp_path / "p.md").read_text()


def test_expect_plan_matches_a_long_plan(tmp_path, monkeypatch):
    from argparse import Namespace
    m, _ = _env(tmp_path, monkeypatch)
    text = "".join(f"row {i}\n" for i in range(300))
    m._plan(Namespace(plan_out="p.md", expect_plan=None), text)
    assert (tmp_path / "p.md").read_text().startswith("⚠ ")
    m._plan(Namespace(plan_out=None, expect_plan="p.md"), text)  # approved file still matches
    with pytest.raises(ValueError, match="plan changed"):
        m._plan(Namespace(plan_out=None, expect_plan="p.md"), text + "x\n")


def test_pr_body_preview_escaped_once():
    plan = prs.render_plan("github", "T", "a|b \\ c", "main", "feat", [1001])
    assert "| body | 7 chars: a\\|b \\\\ c |" in plan


HOSTILE_TITLE = 'x $(touch pwned) `touch pwned2` "q" \'s\' ; rm -rf ~\nline2'


def test_hostile_title_in_prepared_args_reaches_payload_unchanged_and_runs_nothing(tmp_path, monkeypatch):
    m, fake = _env(tmp_path, monkeypatch, "azure", "web", [{"pullRequestId": 9}])
    _prepared(tmp_path, {"title": HOSTILE_TITLE, "base": "main", "head": "feat"})
    _no_subprocess(monkeypatch)
    assert m.main(["pr", "create", "1001", "--from-prepared", "--apply"]) == 0
    assert fake["c"].calls[0]["body"]["title"] == HOSTILE_TITLE + " AB#1001"
    assert not (tmp_path / "pwned").exists() and not (tmp_path / "pwned2").exists()


def test_hostile_title_goes_to_gh_as_one_argv_item(tmp_path, monkeypatch):
    m, fake = _env(tmp_path, monkeypatch, "github")
    _prepared(tmp_path, {"title": HOSTILE_TITLE, "base": "main", "head": "feat"})
    seen = []

    def run(args, **kw):
        seen.append((args, kw))
        return subprocess.CompletedProcess(args, 0, stdout="https://github.com/contoso/x/pull/5\n", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    assert m.main(["pr", "create", "1001", "--from-prepared", "--apply"]) == 0
    assert seen[0][0][seen[0][0].index("--title") + 1] == HOSTILE_TITLE + " AB#1001" and not seen[0][1].get("shell")


def _created(pr_id=7):
    return {"pullRequestId": pr_id, "repository": {"id": "repo-guid", "project": {"id": "proj-guid"}}}


def test_ensure_links_nothing_to_do_when_story_already_linked(cfg):
    c = FakeClient(cfg, [{"value": [{"id": "1001"}]}])
    assert prs.ensure_work_item_links(c, "web", _created(), [1001]) == ([1001], None)
    assert [x["method"] for x in c.calls] == ["GET"]
    assert c.calls[0]["path"].startswith("/Fabrikam/_apis/git/repositories/web/pullrequests/7/workitems?")


def test_ensure_links_adds_the_missing_story_once(cfg):
    c = FakeClient(cfg, [{"count": 0, "value": []}, {}])
    assert prs.ensure_work_item_links(c, "web", _created(), [1001]) == ([1001], None)
    patch = c.calls[1]
    assert patch["method"] == "PATCH" and "/wit/workitems/1001?" in patch["path"]
    assert patch["body"] == [{"op": "add", "path": "/relations/-", "value": {
        "rel": "ArtifactLink", "url": "vstfs:///Git/PullRequestId/proj-guid%2Frepo-guid%2F7",
        "attributes": {"name": "Pull Request"}}}]
    assert len(c.calls) == 2


def test_ensure_links_failures_are_notes_not_errors(cfg):
    class Boom(FakeClient):
        def request(self, method, *a, **k):
            if method == method_to_fail:
                raise RuntimeError("secret detail")
            return super().request(method, *a, **k)

    method_to_fail = "GET"
    linked, note = prs.ensure_work_item_links(Boom(cfg), "web", _created(), [1001])
    assert linked == [] and "could not verify" in note and "secret detail" not in note
    method_to_fail = "PATCH"
    linked, note = prs.ensure_work_item_links(Boom(cfg, [{"value": []}]), "web", _created(), [1001])
    assert linked == [] and "link it by hand" in note and "secret detail" not in note


def test_ensure_links_unreadable_create_response_is_a_note(cfg):
    linked, note = prs.ensure_work_item_links(FakeClient(cfg), "web", {"pullRequestId": 7}, [1001])
    assert linked == [] and "could not verify" in note


def test_azure_pr_plan_says_the_story_link_may_be_added():
    azure = prs.render_plan("azure", "T AB#1", "b", "main", "feat/x", [1])
    github = prs.render_plan("github", "T AB#1", "b", "main", "feat/x", [1])
    assert "the link is added to the story" in azure and "added to the story" not in github


def _board_children(*tags):
    return [{"workItems": [{"id": 50 + i} for i, _ in enumerate(tags)]},
            {"value": [{"id": 50 + i, "fields": {"System.Title": f"[sk:{t}] x", "System.State": "New"}}
                       for i, t in enumerate(tags)]}]


def test_pr_refuses_when_tasks_md_is_empty_but_the_board_tracks_tasks(tmp_path, monkeypatch, capsys):
    # R3 N3: implement emptied tasks.md; "no tasks found" must not read as "nothing to check"
    m, _ = _env(tmp_path, monkeypatch, responses=_board_children("T001", "T002"))
    _spec(tmp_path, "")
    assert m.main(ARGS + ["--plan-out", "plan.md"]) == 2
    err = capsys.readouterr().err
    assert "T001, T002" in err and "unticked" in err


def test_pr_empty_tasks_md_with_allow_incomplete_is_a_draft_that_says_why(tmp_path, monkeypatch):
    m, _ = _env(tmp_path, monkeypatch, responses=_board_children("T001"))
    _spec(tmp_path, "")
    assert m.main(ARGS + ["--allow-incomplete", "--plan-out", "plan.md"]) == 0
    plan = (tmp_path / "plan.md").read_text()
    assert "lists no tasks but 1 Task(s) tracked on the board (T001); was it emptied?" in plan


def test_pr_empty_tasks_md_with_nothing_on_the_board_is_still_not_checked(tmp_path, monkeypatch):
    m, _ = _env(tmp_path, monkeypatch, responses=[{"workItems": []}])
    _spec(tmp_path, "")
    assert m.main(ARGS + ["--plan-out", "plan.md"]) == 0
    assert "not checked (no tasks found in" in (tmp_path / "plan.md").read_text()


# ---- prs._git: bounded, pager-free, and blind to inherited repository selectors ----
def test_git_helper_times_out_and_cannot_run_are_refusals(monkeypatch):
    def slow(cmd, *a, **k):
        assert k["timeout"] == 30 and k["stdin"] == subprocess.DEVNULL and "--no-pager" in cmd
        raise subprocess.TimeoutExpired(cmd, 30)
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(prs._Refuse, match="30 s"):
        prs._git(".", "status")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(PermissionError("x")))
    with pytest.raises(prs._Refuse, match="could not be run"):
        prs._git(".", "status")


def test_git_helper_ignores_inherited_repository_selectors(tmp_path, monkeypatch):
    from tests.conftest import commit, git
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "one", {"f": "1\n"})
    for k, v in (("GIT_DIR", str(tmp_path / "nowhere")), ("GIT_INDEX_FILE", str(tmp_path / "idx")),
                 ("GIT_WORK_TREE", str(tmp_path / "elsewhere")), ("GIT_OBJECT_DIRECTORY", str(tmp_path / "objs"))):
        monkeypatch.setenv(k, v)
    r = prs._git(str(tmp_path), "rev-parse", "--show-toplevel")
    assert r.returncode == 0 and r.stdout.decode().strip() == str(tmp_path.resolve())


def test_github_gets_the_body_that_was_approved_not_a_swapped_file(tmp_path, monkeypatch):
    """P4-12a fix: the body is read once; `gh` receives that text on stdin, so a body file swapped after the approval check is never sent."""
    m, _ = _env(tmp_path, monkeypatch)
    seen = {}

    def run(args, **kw):
        seen["args"], seen["input"] = args, kw.get("input")
        return subprocess.CompletedProcess(args, 0, stdout="https://github.com/contoso/x/pull/5\n", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(m.approvals, "require", lambda *a, **k: (tmp_path / "body.md").write_text("SWAPPED after the approval check\n", encoding="utf-8"))
    assert m.main(ARGS + ["--apply"]) == 0
    assert seen["input"] == "Body text\n" and "SWAPPED" not in seen["input"]
    assert seen["args"][seen["args"].index("--body-file") + 1] == "-" and "body.md" not in seen["args"]


def test_github_body_reaches_gh_as_utf8_bytes(tmp_path, monkeypatch):
    """The body goes on stdin as UTF-8 whatever the locale says (text mode with an explicit encoding), non-ASCII text and an emoji included."""
    m, _ = _env(tmp_path, monkeypatch)
    body = "Change \u2192 done \U0001F680 caf\u00e9\n"
    (tmp_path / "body.md").write_text(body, encoding="utf-8")
    out = tmp_path / "stdin.bin"
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(f'#!/bin/sh\ncat > "{out}"\necho https://github.com/contoso/x/pull/5\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{gh.parent}{os.pathsep}{os.environ['PATH']}")
    import locale
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a, **k: "ascii")  # a locale that could not encode the body
    assert m.main(ARGS + ["--apply"]) == 0
    assert out.read_bytes() == body.encode("utf-8")


# ---- v0.3.2: the plan names the head commit in EVERY mode, so a commit added after approval refuses the apply (Copilot review, spec-kit PR 5312) ----
def _git_repo_with_head(tmp_path):
    git(tmp_path, "init", "-q", "-b", "feat")
    return commit(tmp_path, "docs: first", {"f.txt": "one\n"})


def test_the_terminal_mode_plan_names_the_head_commit(tmp_path, monkeypatch):
    m, _ = _env(tmp_path, monkeypatch, "github", real_head=True)
    _git_repo_with_head(tmp_path)
    full = git(tmp_path, "rev-parse", "refs/heads/feat")
    _prepared(tmp_path)
    assert m.main(["pr", "create", "1001", "--from-prepared", "--push", "--plan-out", ".ado/plan-pr.md"]) == 0
    assert f"| head commit | {full} |" in (tmp_path / ".ado" / "plan-pr.md").read_text()


def test_a_commit_added_after_approval_refuses_the_apply_and_pushes_nothing(tmp_path, monkeypatch, capsys):
    m, fake = _env(tmp_path, monkeypatch, "github", real_head=True)
    _git_repo_with_head(tmp_path)
    _prepared(tmp_path)
    assert m.main(["pr", "create", "1001", "--from-prepared", "--push", "--plan-out", ".ado/plan-pr.md"]) == 0
    commit(tmp_path, "feat: added while paused at approve-pr", {"f.txt": "two\n"})  # the human approved the plan above, not this commit
    real_run, seen = subprocess.run, []

    def run(args, *a, **k):
        if args[:2] in (["git", "push"], ["gh", "pr"]):
            seen.append(args)
            raise AssertionError("an unapproved commit must not be pushed")
        return real_run(args, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    capsys.readouterr()
    assert m.main(["pr", "create", "1001", "--from-prepared", "--push", "--apply", "--expect-plan", ".ado/plan-pr.md"]) == 2
    assert CHANGED in capsys.readouterr().err and seen == [] and fake["c"].calls == []
    assert "| head commit |" in (tmp_path / ".ado" / "plan-pr.md.new").read_text()


def test_an_unchanged_head_still_applies_in_terminal_mode(tmp_path, monkeypatch):
    m, _ = _env(tmp_path, monkeypatch, "github", real_head=True)
    _git_repo_with_head(tmp_path)
    _prepared(tmp_path)
    assert m.main(["pr", "create", "1001", "--from-prepared", "--plan-out", ".ado/plan-pr.md"]) == 0
    real_run, created = subprocess.run, []

    def run(args, *a, **k):
        if args[:3] == ["gh", "pr", "create"]:
            created.append(args)
            return subprocess.CompletedProcess(args, 0, "https://example.invalid/pull/1\n", "")
        return real_run(args, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    assert m.main(["pr", "create", "1001", "--from-prepared", "--apply", "--expect-plan", ".ado/plan-pr.md"]) == 0 and len(created) == 1
