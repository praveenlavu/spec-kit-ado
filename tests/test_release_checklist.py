# P4-18: docs/release-checklist.md pins (D§4.8, D§7 AC 14): the steps in order with who/expected/rollback, the prerequisites, the six cases, the global
# rollback, the commands it names (they must exist) and its honesty about never having run. It is a plan until P4-21 runs it.
import re
from pathlib import Path

import pytest

from ado.__main__ import main

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "release-checklist.md"
TEXT = DOC.read_text(encoding="utf-8")
STEPS = [("1", "`ci` on `main`"), ("2", "the contract job"), ("3", "a private GitHub consumer repository"), ("4", "the same cases with `pipelines/*.yml`"),
         ("5", "acceptance criterion 8"), ("6", "tag, release, move `v0` last")]
GH = {"run list", "run view", "run watch", "run rerun", "workflow run", "repo create", "repo edit", "repo view", "repo delete", "api", "secret set",
      "secret delete", "pr create", "pr checks", "pr view", "pr close", "issue close", "release create", "release delete", "auth refresh", "auth switch"}


def section(title_start):
    m = re.search(rf"^## {re.escape(title_start)}.*?(?=^## |\Z)", TEXT, re.M | re.S)
    assert m, title_start
    return m.group(0)


def code_lines():
    out = []
    for block in re.findall(r"```(?:bash)?\n(.*?)```", TEXT, re.S):
        joined = block.replace("\\\n", " ")
        out += [ln.strip() for ln in joined.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return out


def test_it_says_plainly_what_has_run():
    assert "**Status: IN PROGRESS.**" in TEXT and "never run on a hosted runner" in TEXT and "P4-21" in TEXT
    assert "Owner decision, 2026-11-03" in TEXT and "is skipped for 0.3.0" in TEXT  # the live comment-mode proof
    sheet = section("Record sheet")
    rows = sheet.count("| | NOT RUN |")
    assert rows == 1 and sheet.count("NOT RUN") == rows + 1  # step 4 (Azure Pipelines), and the sentence above the table
    assert sheet.count("| PASS |") == 2 and "PASS (filings pending)" in sheet and "run 42441029679" in sheet  # steps 6.1-6.5 and 6.6-6.8: evidence
    assert "PASS (a to d), SKIPPED (e, f)" in sheet and "run 42431132933" in sheet  # step 3: cases a-d on a hosted runner
    assert sheet.count("PASS (pre-RC)") == 2 and "run 41563615813" in sheet and "run 41562104349" in sheet  # steps 1 and 2: evidence, not assertion
    assert "| SKIPPED |" in sheet  # step 5
    assert "| 6.5 strict-verify (c) and (d) through the shipped file |" in sheet  # the only part of step 3 that runs after the flip
    assert not re.search(r"\b(passed on|ran green|verified on) a hosted runner", TEXT, re.I)


def test_the_steps_are_the_design_steps_in_order():
    heads = re.findall(r"^## Step (\d): (.*)$", TEXT, re.M)
    assert len(heads) == len(STEPS)
    for (n, want), (num, title) in zip(STEPS, heads):
        assert num == n and want in title, (n, title)
    assert TEXT.index("## Before you start") < TEXT.index("## Step 1") < TEXT.index("## Step 6") < TEXT.index("## Global rollback") < TEXT.index("## Record sheet")


@pytest.mark.parametrize("n,_", STEPS)
def test_every_step_has_who_expected_and_a_rollback(n, _):
    sec = section(f"Step {n}:")
    for marker in ("**Who:**", "**Expected:**", "**Rollback:**"):
        assert sec.count(marker) == 1, (n, marker)
    assert re.search(r"\*\*Who:\*\* (Owner|Controller|each sub-step)", sec)
    assert "```" in sec  # exact commands, not prose
    assert sec.index("**Who:**") < sec.index("**Expected:**") < sec.index("**Rollback:**")


def test_before_you_start_lists_every_prerequisite():
    pre = section("Before you start")
    for needle in ("CI billing is unlocked", "Actions is enabled", "Python matrix exists", "3.9", "3.13", "ubuntu-24.04", "requires-python >=3.9", "contract canary",
                   "go-public audit", "python3 scripts/scrub_check.py --require-denylist --history", "python3 scripts/ci_smoke/run_local.py",
                   "spec-kit-ado-smoke", "Owner", "ADO_REQUIRE_BASELINE=1", "a green leg means they ran", "Contents: Read-only"):
        assert needle in pre, needle
    assert "are both green on a hosted runner" not in pre  # that is step 1's result, not a prerequisite
    assert pre.count("\n1. ") == 1 and "\n7. " in pre


def test_step_1_names_the_matrix_jobs_of_ci_yml():
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    sec = section("Step 1:")
    for v in wf["jobs"]["test"]["strategy"]["matrix"]["python-version"]:
        assert f"`test ({v})`" in sec
    assert "`scrub`" in sec and "`actionlint`" in sec and "--require-denylist" in sec
    assert "gh run list -R praveenlavu/spec-kit-ado --workflow ci --branch main --event push --commit" in sec
    assert "full-history checkout" in sec and "a0b3c9e" in sec and "f83c868" in sec and "depth-1" in sec


def test_step_2_runs_the_contract_workflow_on_both_spec_kit_versions():
    sec = section("Step 2:")
    assert "gh workflow run speckit-contract.yml -R praveenlavu/spec-kit-ado --ref main" in sec
    assert "9dca9bf42a031765ee5e5f2371137f2f26967e11" in sec and "latest" in sec and "skipped" in sec
    assert "--event workflow_dispatch" in sec and "`headSha` equals `RC`" in sec  # the run listed is the one just started


def _cells(row):
    return [c.strip() for c in row.strip().strip("|").split(" | ")]


# The patterns the last column documents. `gh run view --log` prints each step's own script too, so a pattern that is a plain text of the script would
# match without the step having run: the ones whose text IS in a template are anchored to the rendered output (`##[error]`, `##[warning]`, a line end).
LOG_PATTERNS = {"a": [r"##\[error\]Branch .* is not tied to a work item"], "b": ["stack-profile-drift"], "c": [r"spec-kit-ado: strict verify passed\.$"],
                "d": ["green-fails"], "e": [r"[0-9]+: DoD complete - ledger (was already )?attached, story closed\.$", r"##\[warning\][0-9]+: done cross-check"],
                "f": ["decisions-xcheck-marker"]}
TEMPLATES = {"a": "ado-branch-guard", "b": "ado-gate", "c": "ado-strict-verify", "d": "ado-strict-verify", "e": "ado-link-on-merge", "f": "ado-link-on-merge"}
# one rendered line of the real log for each pattern (annotations render as ##[error]/##[warning]; the timestamp prefix is GitHub's)
RENDERED = {"a": "2026-11-02T10:00:00.0000000Z ##[error]Branch 'feature/members-list' is not tied to a work item (expected <branch_prefix>ab<id>-<slug>)",
            "c": "2026-11-02T10:00:00.0000000Z spec-kit-ado: strict verify passed.",
            "e": "2026-11-02T10:00:00.0000000Z 1001: DoD complete - ledger attached, story closed."}


def test_step_3_has_the_six_cases_with_their_local_and_hosted_results_and_the_log_text_each_must_show():
    sec = section("Step 3:")
    rows = {m.group(1): m.group(0) for m in re.finditer(r"^\| \(([a-f])\) \|.*$", sec, re.M)}
    assert sorted(rows) == list("abcdef")
    for letter, rule in {"b": "stack-profile-drift", "d": "green-fails", "f": "decisions-xcheck-marker"}.items():
        assert rule in rows[letter], letter
    assert "ado branch --id-from" in rows["a"] and "`ado done` (preview)" in rows["e"] and "does not name a posting of the kit" in rows["f"]
    assert "`ready: true`" in rows["e"] and "strict mode from the target branch" in rows["c"]
    for letter, patterns in LOG_PATTERNS.items():  # a red run for the wrong reason is not a match: every case names the pattern its log must match
        cells = _cells(rows[letter])
        assert len(cells) == 5 and all(f"`{pat}`" in cells[4] for pat in patterns), (letter, cells[-1])
    assert "present: " in rows["e"] and "absent: " in rows["e"]
    assert "| The log must match |" in sec
    assert "A red run is recorded as a match only if" in sec and "toolkit checkout and install steps of that job succeeded" in sec
    assert "gh run view <databaseId> -R praveenlavu/ci-smoke-consumer --log | grep -E '<pattern from the last column>'" in sec
    assert "anchored to what `gh run view --log` *renders*" in sec and "a red guard counts\nonly when the `##[error]Branch` annotation is present" in sec
    assert "pinned to `RC`" in sec and "not `@v0`" in sec and "scripts/ci_smoke/" in sec and "BLOCKED (toolkit private)" not in sec


def test_step_3_sets_both_secrets_as_owner_steps_without_a_value_in_argv():
    sec = section("Step 3:")
    prep = sec[sec.index("**Prepare (once):**"):sec.index("Why `TOOLKIT_READ_TOKEN`")]
    for needle in ("gh api repos/praveenlavu/spec-kit-ado/actions/permissions/access --jq .access_level", "the rollback restores THAT value",
                   "gh repo create praveenlavu/ci-smoke-consumer --private --clone", "gh secret set ADO_TOKEN -R praveenlavu/ci-smoke-consumer",
                   "gh secret set TOOLKIT_READ_TOKEN --repo praveenlavu/ci-smoke-consumer", "# Owner: both PATs are created", "Contents Read-only",
                   "in argv (no --body), never printed, never in chat", "-f access_level=user", "owned by the same\n# user"):
        assert needle in prep, needle
    assert "--body" not in "\n".join(ln for ln in prep.splitlines() if ln.startswith("gh "))
    assert "secrets.TOOLKIT_READ_TOKEN || github.token" in sec and "`github.token` of the consumer cannot read" in sec
    assert "`TOOLKIT_READ_TOKEN: ${{ secrets.TOOLKIT_READ_TOKEN }}` next to `ADO_TOKEN` under `secrets:`" in sec
    assert "**Who:** Owner for creating the two PATs and setting them as secrets" in sec


def test_step_3_vendors_the_strict_verify_template_with_exactly_two_edits_and_the_edit_matches_the_template():
    sec = section("Step 3:")
    assert 'git -C "$TOOLKIT" show "$RC:.github/workflows/ado-strict-verify.yml" > .github/workflows/ado-strict-verify.yml' in sec
    assert '| diff - .github/workflows/ado-strict-verify.yml' in sec and "exactly those two additions" in sec
    assert "uses: ./.github/workflows/ado-strict-verify.yml" in sec and "does **not** prove the cross-repository" in sec
    # the python snippet must really apply to the shipped template: each anchor occurs once, and the result still parses as a workflow
    import yaml
    code = re.search(r"python3 - <<'PY'\n(.*?)\nPY\n", sec, re.S).group(1)
    template = (ROOT / ".github" / "workflows" / "ado-strict-verify.yml").read_text(encoding="utf-8")
    ns = {}
    exec(code.split("p.write_text")[0].replace('p = pathlib.Path(".github/workflows/ado-strict-verify.yml")\nt = p.read_text()', "t = TEMPLATE"), {"TEMPLATE": template, "pathlib": None}, ns)
    out = template.replace(ns["secret_a"], ns["secret_b"]).replace(ns["token_a"], ns["token_b"])
    wf = yaml.safe_load(out)
    assert wf[True]["workflow_call"]["secrets"] == {"TOOLKIT_READ_TOKEN": {"required": False}}
    toolkit = [st for st in wf["jobs"]["verify"]["steps"] if st.get("with", {}).get("repository") == "praveenlavu/spec-kit-ado"]
    assert len(toolkit) == 1 and toolkit[0]["with"]["token"] == "${{ secrets.TOOLKIT_READ_TOKEN || github.token }}"
    assert len(out.splitlines()) - len(template.splitlines()) == 4  # exactly three lines of secret declaration and one token line


def test_step_3_rollback_undoes_every_setting_it_made():
    sec = section("Step 3:")
    back = sec[sec.index("**Rollback:**"):]
    for needle in ('gh pr close <number> -R praveenlavu/ci-smoke-consumer --delete-branch --comment "ci smoke case withdrawn"',
                   "gh secret delete ADO_TOKEN -R praveenlavu/ci-smoke-consumer", "gh secret delete TOOLKIT_READ_TOKEN -R praveenlavu/ci-smoke-consumer",
                   "gh api -X PUT repos/praveenlavu/spec-kit-ado/actions/permissions/access -f access_level=<the value recorded in Prepare>       # Owner: restores the earlier setting",
                   'ado update <story> --state "<the state it had before>"', "gh auth refresh -s delete_repo && gh repo delete praveenlavu/ci-smoke-consumer --yes",
                   "gh auth refresh --remove-scopes delete_repo", "revokes the GitHub PAT", "Azure DevOps\nPAT"):
        assert needle in back, needle
    assert back.index("gh repo delete") < back.index("--remove-scopes delete_repo")  # the scope comes off right after it was used
    assert "access_level=none" not in back  # never assume what the setting was before
    assert sec.index("--jq .access_level") < sec.index("-f access_level=user")  # the earlier value is read before it is changed


def test_the_cases_in_the_table_are_the_fixtures_titles_letters():
    import json
    cases = json.loads((ROOT / "scripts" / "ci_smoke" / "cases.json").read_text(encoding="utf-8"))
    assert list(cases) == list("abcdef")
    sec = section("Step 3:")
    for name in cases:
        assert f"| ({name}) |" in sec


def test_step_4_covers_the_pipeline_half_and_the_same_cases():
    sec = section("Step 4:")
    for needle in ("spec-kit-ado-smoke", "az repos pr create", "az pipelines runs list", "az pipelines runs show", "az repos pr update", "az pipelines delete",
                   "(a)", "(f)", "decisions-xcheck-marker", "Merge (no fast-forward)", "az devops login --organization https://dev.azure.com/<org>",
                   "AZURE_DEVOPS_EXT_PAT", "never passed in argv"):
        assert needle in sec, needle
    back = sec[sec.index("**Rollback:**"):]
    assert "az repos pr update --organization https://dev.azure.com/<org> --id <pr> --status abandoned" in back and "az pipelines delete" in back and "ado update <story>" in back


def test_step_5_follows_the_unattended_doc():
    sec = section("Step 5:")
    assert "unattended.md" in sec and "ado approvals sync <run-id> --resume" in sec and "no TTY" in sec and "launchctl unload" in sec


def _sub_steps():
    sec = section("Step 6:")
    parts = re.split(r"\n(?=\*\*6\.\d )", sec)[1:]
    return {p[4]: p for p in parts}


OWNERS = {"1": "Controller", "2": "Controller", "3": "Owner", "4": "Owner", "5": "Controller", "6": "Owner", "7": "Owner", "8": "Owner"}


def test_step_6_every_sub_step_has_exactly_one_who_and_the_public_ones_are_the_owners():
    subs = _sub_steps()
    assert sorted(subs) == list("12345678")
    for n, part in subs.items():
        assert re.findall(r"Who: (Owner|Controller)\b", part) == [OWNERS[n]], n  # one actor, no contradiction
    head = section("Step 6:").split("**6.1 ")[0]
    assert "The Owner does 6.3, 6.4, 6.6, 6.7 and 6.8; the Controller does 6.1, 6.2 and 6.5." in head and "6.4 is the point of no return" in head
    assert "Controller for 6.1 to 6.3" not in section("Step 6:")


def test_step_6_owner_steps_run_in_the_clone_where_the_tag_and_tmp_live():
    sec = section("Step 6:")
    assert "run **in one clone on one machine**" in sec and "`RC`, the local tag `v0.3.0` and the directory `tmp` that 6.2 creates live there" in sec
    subs = _sub_steps()
    for n in "36":
        assert "in the clone where 6.1 ran, on the same machine" in subs[n], n
    assert "Run in the same clone as 6.1" in subs["8"]


def test_the_tag_block_is_a_subshell_in_both_documents_so_exit_cannot_close_a_pasted_shell():
    for text in (section("Step 6:"), (ROOT / "docs" / "catalog-submission.md").read_text(encoding="utf-8")):
        block = re.search(r"```bash\n(\s*\(\n.*?git tag -a v0\.3\.0.*?\n\s*\)\n)\s*```", text, re.S)
        assert block, "the tag block must be wrapped in ( ... )"
        lines = [ln.strip() for ln in block.group(1).splitlines()]
        assert lines[0] == "(" and lines[1] == "set -e" and lines[-1] == ")" and any("exit 1" in ln for ln in lines)
        assert not [ln for ln in text.splitlines() if ln.startswith(("exit 1", "  exit 1"))]  # no bare top-level exit anywhere
    pack = (ROOT / "docs" / "catalog-submission.md").read_text(encoding="utf-8")
    assert re.search(r"git show v0\.3\.0:extension\.yml \| grep -E 'version: \"0\.3\.0\"'\n\s*git push origin v0\.3\.0\n\s*\)", pack)  # the push is the subshell's last line
    assert "never closes the shell you pasted it into" in pack and "never closes that shell" in section("Step 6:")


def test_step_6_order_tag_the_rc_and_v0_last():
    sec = section("Step 6:")
    order = [f"**6.{n} " for n in range(1, 9)]
    assert [sec.index(o) for o in order] == sorted(sec.index(o) for o in order)
    subs = _sub_steps()
    assert 'git tag -a v0.3.0 -m "spec-kit-ado 0.3.0" "$RC"' in subs["1"] and "main\n" not in subs["1"].split("```")[1]  # the verified RC, not the moving main
    assert "test \"$(git rev-parse 'v0.3.0^{commit}')\" = \"$RC\"" in subs["1"] and "never the moving `main`" in subs["1"]
    assert "(cd \"$tmp/out\" && shasum -a 256 ado-0.3.0.zip | tee SHA256SUMS)" in subs["2"] and "shasum -a 256 -c SHA256SUMS" in subs["2"] and "ado-0.3.0.zip: OK" in subs["2"]
    assert "git push origin v0.3.0" in subs["3"]
    assert "gh repo edit praveenlavu/spec-kit-ado --visibility public --accept-visibility-change-consequences" in subs["4"] and "PUBLIC" in subs["4"]
    assert "--verify-tag" in subs["6"] and "tmp=<the directory printed by 6.2>" in subs["6"] and "GitHub never creates one at the tip of the default branch" in subs["6"]
    assert sec.index("git push origin v0.3.0") < sec.index("--visibility public") < sec.index("strict verify passed") < sec.index("gh release create") < sec.index("git tag -f v0 v0.3.0")  # v0 moves last


def test_step_6_5_is_the_shipped_template_rerun_with_a_stop_rule():
    part = _sub_steps()["5"]
    for needle in ("uses: praveenlavu/spec-kit-ado/.github/workflows/ado-strict-verify.yml@<RC>", "no vendored copy and no token",
                   "--log | grep -E 'spec-kit-ado: strict verify passed\\.$'", "--log | grep -F 'green-fails'", "(c) green, and its log has a line matching",
                   "(d) **fails**, its log shows `green-fails`, and it has **no** line matching the (c) pattern", "**STOP**",
                   "The Owner deletes the tag", "a tag push and so owner-only", "the\ncontroller then removes the local one with `git tag -d v0.3.0`", "Do not run 6.6 to 6.8", "**no GitHub release**", "`v0` is not moved",
                   "git push origin :refs/tags/v0.3.0", "git tag -d v0.3.0", "The owner decides whether to make the repository private again",
                   "--visibility\nprivate --accept-visibility-change-consequences", "0.3.1"):
        assert needle in part, needle
    assert "(c) is red, or (d) is green" in part  # a false green of (d) stops the release too


def test_every_step_6_rollback_line_is_pinned():
    subs = _sub_steps()
    assert "*Rollback:* `git tag -d v0.3.0`" in subs["1"]
    assert '*Rollback:*\n\n```bash\ngit worktree remove --force "$tmp/src"\nrm -rf "$tmp/out"\n```' in subs["2"]
    assert "*Rollback:* before anything consumed it, `git push origin :refs/tags/v0.3.0` and `git tag -d v0.3.0`" in subs["3"]
    assert '*Rollback:* [Global rollback](#global-rollback), "Making the repository private again"' in subs["4"]
    assert "*Rollback:* as step 3." in subs["5"]
    assert '*Rollback:* [Global rollback](#global-rollback),\n"A bad tag or release"' in subs["6"]
    assert '*Rollback:* "Retracting a catalog filing" below.' in subs["7"]
    assert "git ls-remote --tags origin v0\ngit tag -f v0 v0.3.0 && git push -f origin v0" in subs["8"] and "git tag -f v0 <previous tag> && git push -f origin v0" in subs["8"]
    assert "`git push origin :refs/tags/v0` and `git tag -d v0`" in subs["8"] and "`v0` did not exist before" in subs["8"]
    sec = section("Step 6:")
    assert sec.count("**Rollback:**") == 1 and "[Global rollback](#global-rollback). Before 6.3 the only undo needed is `git tag -d v0.3.0`." in sec


def test_the_owner_only_steps_are_marked_owner():
    assert "# Owner: both PATs are created in the owner's browser" in TEXT and "# Owner: lets the private consumer" in TEXT
    assert "an agent never types a token or passes one in argv" in TEXT and "every push of a release tag" in TEXT
    assert "Retracting a catalog filing** (Owner" in TEXT and "Making the repository private again** (Owner)" in TEXT
    for item in ("the repository visibility flip", "every\n  catalog filing", "`ADO_TOKEN`,\n  `TOOLKIT_READ_TOKEN`", "the `delete_repo` scope"):
        assert item in TEXT[:TEXT.index("## Before you start")], item


def test_global_rollback_has_each_undo_with_a_command_that_exists():
    sec = section("Global rollback")
    for needle in ("x.y.z+1", "`v0.3.1`", "git tag -f v0 <previous tag> && git push -f origin v0", "git ls-remote origin refs/tags/v0",
                   "gh release delete v0.3.0 -R praveenlavu/spec-kit-ado --yes", "git push origin :refs/tags/v0.3.0", "git tag -d v0.3.0",
                   "gh repo edit praveenlavu/spec-kit-ado --visibility private --accept-visibility-change-consequences", "prints PRIVATE",
                   "cannot recall what was already cloned", "gh pr close <number> -R github/spec-kit --delete-branch", "gh issue close <number> -R github/spec-kit",
                   "never moved or reused", "pin a SHA"):
        assert needle in sec, needle


def test_every_gh_command_is_a_known_subcommand():
    seen = set()
    for ln in code_lines():
        m = re.match(r"gh ([a-z]+)(?: ([a-z]+))?", ln)
        if m:
            sub = f"{m.group(1)} {m.group(2)}" if f"{m.group(1)} {m.group(2)}" in GH else m.group(1)
            assert sub in GH, ln
            seen.add(sub)
    assert {"run list", "run view", "run watch", "workflow run", "repo edit", "release create", "release delete", "pr close", "issue close"} <= seen


def _help(argv, capsys):
    with pytest.raises(SystemExit) as e:
        main([*argv, "--help"])
    assert e.value.code == 0
    return capsys.readouterr().out


def test_every_ado_command_and_flag_exists(capsys):
    seen = []
    for ln in code_lines():
        if not re.match(r"ado [a-z]", ln):
            continue
        verbs = []
        for t in ln.split()[1:]:
            if not re.fullmatch(r"[a-z]+", t) or len(verbs) == 2 or (verbs and verbs[0] not in ("approvals", "strict")):
                break
            verbs.append(t)
        out = _help(verbs, capsys)
        for flag in re.findall(r"(?<![\w-])(--[a-z-]+)", ln):
            assert flag in out, (ln, flag)
        seen.append(verbs)
    assert ["approvals", "sync"] in seen and ["status"] in seen and ["update"] in seen


def test_the_tag_release_and_v0_commands_equal_the_go_public_checklist_of_catalog_submission():
    other = (ROOT / "docs" / "catalog-submission.md").read_text(encoding="utf-8")
    flat = lambda s: re.sub(r"\s+", " ", s.replace("\\\n", " "))  # noqa: E731
    sec, other_flat = flat(section("Step 6:")), flat(other)
    for cmd in ('git tag -a v0.3.0 -m "spec-kit-ado 0.3.0" "$RC"',
                'test "$(git rev-parse \'v0.3.0^{commit}\')" = "$RC" || { echo "the tag is not the release candidate - do not push"; exit 1; }',
                "git show v0.3.0:extension.yml | grep -E 'version: \"0.3.0\"'",
                "git push origin v0.3.0",
                'specify bundle validate --path "$tmp/src/bundle" --offline',
                'specify bundle build --path "$tmp/src/bundle" --output "$tmp/out"',
                '(cd "$tmp/out" && shasum -a 256 ado-0.3.0.zip | tee SHA256SUMS)',
                '(cd "$tmp/out" && shasum -a 256 -c SHA256SUMS)',
                'gh release create v0.3.0 "$tmp/out/ado-0.3.0.zip" "$tmp/out/SHA256SUMS" -R praveenlavu/spec-kit-ado --title "v0.3.0" --notes-file <file holding the CHANGELOG 0.3.0 section> --verify-tag',
                "git tag -f v0 v0.3.0 && git push -f origin v0"):
        assert cmd in sec and cmd in other_flat, cmd  # the two documents must not drift
    assert "main\n" not in other.split("git tag -a v0.3.0")[1].split("```")[0].split("\n")[0] + "\n"  # neither document tags the moving main
    assert "does not exist yet" not in other and "(release-checklist.md)" in other and "written, never run" in other  # the pack points at the checklist
    for f in ("extension.yml", "bundle/bundle.yml", "catalog/extensions.json", "catalog/workflows.json", "workflows/ado-lifecycle/workflow.yml",
              "workflows/ado-lifecycle/README.md", "README.md", "LICENSE"):
        assert f in sec and f in other


def test_local_links_and_anchors_resolve():
    def slug(h):
        return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", h.strip().lower()))
    for target in re.findall(r"\]\(([^)]+)\)", TEXT):
        if target.startswith(("http", "#")):
            continue
        path, _, anchor = target.partition("#")
        f = (DOC.parent / path).resolve()
        assert f.exists(), target
        if anchor:
            heads = [slug(h) for h in re.findall(r"^#+ (.*)$", f.read_text(encoding="utf-8"), re.M)]
            assert anchor in heads, target
    for anchor in re.findall(r"\]\(#([^)]+)\)", TEXT):
        assert anchor in [slug(h) for h in re.findall(r"^#+ (.*)$", TEXT, re.M)], anchor


def test_the_hosted_runner_claim_matches_the_submission_pack():
    # the submission pack says the checklist does not exist yet / is run later; it must not be contradicted by this file's own claims
    cs = (ROOT / "docs" / "catalog-submission.md").read_text(encoding="utf-8")
    assert "never run on a hosted runner" in TEXT and "in a consumer repository on a hosted runner" in cs and "have never run there" in cs


def test_the_checklist_is_a_repository_only_doc_so_it_may_link_to_repository_files():
    assert "docs/release-checklist.md" in (ROOT / ".extensionignore").read_text(encoding="utf-8").splitlines()


def _template_text(letter):
    return (ROOT / ".github" / "workflows" / f"{TEMPLATES[letter]}.yml").read_text(encoding="utf-8")


def test_no_log_pattern_matches_the_workflows_own_script_text():
    """`gh run view --log` prints every executed step's script. A pattern that matches the template file itself would be satisfied by the script
    alone (a branch guard that failed at `pip install` still prints its `echo "::error::... is not tied to a work item"`)."""
    for letter, patterns in LOG_PATTERNS.items():
        for pat in patterns:
            assert not re.search(pat, _template_text(letter), re.M), (letter, pat)


def test_a_plain_text_that_is_in_the_template_is_only_checked_through_an_anchored_pattern():
    plain = {"a": "is not tied to a work item", "c": "strict verify passed", "e": "DoD complete"}
    for letter, text in plain.items():
        assert text in _template_text(letter), (letter, text)  # the trap is real: the script holds the words
        pats = LOG_PATTERNS[letter]
        assert pats and not any(pat == text for pat in pats)
        assert any(("##\\[error\\]" in pat) or pat.endswith(r"\.$") for pat in pats), (letter, pats)  # an annotation prefix or a line-end anchor
    assert "done cross-check" in _template_text("e") and "##\\[warning\\]" in LOG_PATTERNS["e"][1]
    for letter in "bdf":  # these texts are produced at run time only, so their plain form is already specific
        assert LOG_PATTERNS[letter][0] not in _template_text(letter), letter


def test_the_anchored_patterns_do_match_the_lines_the_log_renders():
    for letter, line in RENDERED.items():
        assert any(re.search(pat, line) for pat in LOG_PATTERNS[letter]), (letter, line)
    # ... and the script's own echo does not satisfy them
    assert not re.search(LOG_PATTERNS["c"][0], '            echo "spec-kit-ado: strict verify passed."; exit 0; fi')
    assert not re.search(LOG_PATTERNS["a"][0], """echo "::error::Branch '$(printf '%s' "$BRANCH" | ann)' is not tied to a work item (expected""")
    assert not re.search(LOG_PATTERNS["e"][0], 'closed-attached) echo "$id: DoD complete - ledger attached, story closed." ;;')
    assert re.search(LOG_PATTERNS["e"][1], "2026-11-02T10:00:00.0000000Z ##[warning]1001: done cross-check: decisions-xcheck-marker")  # the absent pattern finds a real warning
    assert not re.search(LOG_PATTERNS["e"][1], '            [ -z "$xw" ] || echo "::warning::$id: done cross-check: $xw"')
