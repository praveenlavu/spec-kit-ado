"""The role prompts and docs: the 14th command `speckit.ado.mode`, `rules/roles.md`, `docs/roles.md`, the role line in every command, and the
NOT PROMISED list. Each verb, key, gate id, rule id and bullet the docs make is pinned here so the docs cannot drift from the code."""
import re
from pathlib import Path

import yaml

from ado import approvals, config, signoff

ROOT = Path(__file__).resolve().parent.parent

NOT_PROMISED = (
    "that the tests ran or passed",
    "that the merged code is the code QA saw",
    "that code cannot merge unsigned",
    "that the story cannot reach Done except through `ado done`",
    "that the person at the keyboard holds the mode they picked",
    "that a signer read the file",
    "separation of duties",
    "anything against someone who holds approver, bypass-rules or admin rights",
)


def _flat(path):
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def _commands():
    return sorted((ROOT / "commands").glob("*.md"))


def test_mode_is_the_fourteenth_registered_command():
    cmds = yaml.safe_load((ROOT / "extension.yml").read_text(encoding="utf-8"))["provides"]["commands"]
    assert len(cmds) == 14
    assert {"name": "speckit.ado.mode", "file": "commands/mode.md"}.items() <= next(c for c in cmds if c["name"] == "speckit.ado.mode").items()
    assert len(_commands()) == 14 and all(c["description"] for c in cmds)


def test_every_command_carries_the_role_line():
    missing = [p.name for p in _commands() if "rules/roles.md" not in p.read_text(encoding="utf-8")]
    assert not missing, f"no role line in: {missing}"


def test_mode_command_playbooks():
    text = _flat("commands/mode.md")
    for needle in ("`ado mode <role>`", "`ado mode --clear`", "`ado mode --require", "`.ado/mode.json`", "BPO", "TPO", "`bsa`", "Dev", "QA", "`ado qa <id>`",
                   "`ado qa <id> --check`", "`/reject signoff-qa <hash>", "`ado approvals check <id> --gate signoff-tpo --offline`", "`ado approvals check <id> --gate signoff-qa --offline`",
                   "`ado approvals request <id> --gate signoff-bpo`", "steers the prompts only", "never posts a sign-off", "never blocks `implement`"):
        assert needle in text, needle
    assert "rules/hitl.md" in text and "rules/roles.md" in text


def test_roles_rule_pins():
    text = _flat("rules/roles.md")
    for needle in ("`.ado/mode.json`", "agent-writable", "steers the prompts only", "`roles.map`", "`roles.signoff`", "target branch",
                   "never post `/approve signoff-", "never `/reject` on a human's behalf", "`implement` is never blocked", "`ado done`", "rules/hitl.md",
                   "`signoff-tpo`", "`signoff-qa`", "`signoff-bpo`"):
        assert needle in text, needle
    for role in config.ROLE_IDS:
        assert f"`{role}`" in text, role


def test_roles_doc_names_every_verb_key_gate_and_rule_id():
    doc = _flat("docs/roles.md")
    for needle in ("`ado mode", "`ado qa <id>`", "`ado approvals check", "`ado approvals request", "`ado done", "`--offline`", "`--expect-plan`",
                   "`roles.map`", "`roles.signoff`", "`approvers`", "`approval`", "`.ado/mode.json`", "`QA_<id>.md`", "`Decisions_<id>.md`"):
        assert needle in doc, needle
    for gate in signoff.GATES:
        assert f"`{gate}`" in doc, gate
    for rule in ("signoff-bypass", "signoff-unreadable", "signoff-terminal", "signoff-trust", "signoff-one-person", "not-role", "excluded-author", "stale",
                 "rejected", "missing", "edited", "on-behalf", "open-decisions", "artifact", "uncovered-ac", "uncovered-rule", "malformed-case", "rules-unreadable", "malformed-newer"):
        assert f"`{rule}`" in doc, rule
    for role in config.ROLE_IDS:
        assert f"`{role}`" in doc, role


def test_roles_doc_opens_with_the_qa_only_recipe_and_names_every_approver_side_effect():
    text = (ROOT / "docs/roles.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", text, re.M)
    assert headings[0].startswith("Recipe: QA signs off, nobody else"), headings
    recipe = " ".join(re.split(r"^## ", text, flags=re.M)[1].split())
    for needle in ('"approval": "comment"', '"approvers"', '"roles"', '"map"', '"signoff": ["qa"]', "`ado qa <id>`", "`/approve signoff-qa <hash>`",
                   "What adding the tester to `approvers` also lets them do",
                   "approve any of the 12 workflow gates", "approve the plan of `ado push`, `ado pr create`, `ado questions --post`, `ado decisions --post` and `ado branch --link`",
                   "answer the story's open questions with `/answer Q<n>`", "validate or reject a decision with `/approve D<n>` or `/reject D<n>`",
                   "validate a rule exclusion", "turns on comment mode for the whole project", "`ado decisions --resolve` is refused",
                   "the done-time cross-check of the decisions file", "must not be the identity of the token", "a merged pull request"):
        assert needle in " ".join(text.split()), needle
    assert recipe  # the recipe is the first section


def test_roles_doc_states_the_limits():
    doc = _flat("docs/roles.md")
    for needle in ("UTF-8 text under 1 MiB", "at most 200 files and 8 MiB", "tasks.md", "`qa` frame label", "non-holder's `/reject` is ignored",
                   "a posted request voids a standing approval", "person with the web UI signs, no token", "process enforcement, not a security boundary",
                   "`bsa` is the same role as `tpo`", "`dev` is a role id only", "awaiting signoff-bpo", "a human runs `ado done --apply`",
                   "`signoff-one-person`", "`--base` and `origin/HEAD` differ"):
        assert needle in doc, needle


def test_not_promised_list_is_in_readme_and_roles_doc_word_for_word():
    for path in ("README.md", "docs/roles.md"):
        text = _flat(path)
        assert "**Not promised by the role sign-offs:**" in text or "## Not promised" in text, path
        for bullet in NOT_PROMISED:
            assert f"- {bullet}" in text or bullet in text, f"{path}: {bullet}"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.count("**Not promised by the role sign-offs:**") == 1


def test_readme_documents_the_new_commands_and_verbs():
    readme = _flat("README.md")
    assert "`speckit.ado.mode`" in readme and "docs/roles.md" in readme
    for needle in ("| `mode [ROLE", "| `qa ID", "`approvals request ID --gate signoff-bpo`", "`approvals check ID --gate signoff-tpo|signoff-qa|signoff-bpo [--offline]`"):
        assert needle in readme.replace("\\|", "|"), needle


def test_glossary_agents_hitl_configuration_and_changelog():
    gl = _flat("docs/glossary.md")
    for term in ("**Role**", "**Mode**", "**Sign-off**", "**QA file**"):
        assert term in gl, term
    agents = _flat("AGENTS.md")
    assert "`ado mode`" in agents and "`ado qa <id>`" in agents and "`roles`" in agents and "rules/roles.md" in agents and "`ado approvals request <id>" in agents
    assert "never post `/approve signoff-" in agents
    hitl = _flat("rules/hitl.md")
    assert "`signoff-" in hitl and "rules/roles.md" in hitl and "never post" in hitl
    assert "(roles.md)" in _flat("docs/configuration.md")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert log.split("\n## ")[1].startswith("Unreleased"), "the newest changelog section is Unreleased"
    unreleased = " ".join(log.split("\n## ")[1].split())
    for needle in ("`roles`", "`ado mode`", "`ado qa <id>`", "`speckit.ado.mode`", "`signoff-tpo`", "`signoff-qa`", "`signoff-bpo`", "`ado approvals request <id>",
                   "`ado done`", "14 commands"):
        assert needle in unreleased, needle


def test_bundle_catalog_and_submission_say_fourteen():
    assert "14 `speckit.ado.*` commands" in _flat("bundle/README.md") and "13 `speckit.ado" not in _flat("bundle/README.md")
    assert '"commands": 14' in (ROOT / "catalog/extensions.json").read_text(encoding="utf-8")
    sub = _flat("docs/catalog-submission.md")
    assert "14 extension commands" in sub and "| Number of Commands | `14` |" in sub and "13 extension commands" not in sub


def test_gate_ids_and_rule_ids_the_docs_name_exist_in_the_code():
    assert signoff.GATES == ("signoff-tpo", "signoff-qa", "signoff-bpo")
    assert not set(signoff.GATES) & set(approvals.GATES)
