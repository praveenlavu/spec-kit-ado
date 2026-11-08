"""Catalog readiness: workflow folder, self-hosted catalogs, submission draft (N7)."""
import json
import re
from pathlib import Path

import yaml

import ado
from tests.conftest import CHANGELOG_030_HEADING, heading_030_index

ROOT = Path(__file__).resolve().parent.parent
V = ado.__version__
REPO = "https://github.com/praveenlavu/spec-kit-ado"
RAW = f"https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v{V}"
# The submission pack is written for the release target; the code moves to it at release prep (no bump before).
# Release prep (R-1) flipped RELEASE_PREP_PENDING to False in the commit that bumped every version to DOC_V:
# the tests below require the code, manifests, catalogs and the pack's tool floor to agree on DOC_V.
RELEASE_PREP_PENDING = False
PREV_V = "0.2.0"  # the only code version accepted while RELEASE_PREP_PENDING is True
DOC_V = "0.3.1"  # the current release
DOC_RAW = f"https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v{DOC_V}"
PACK_V = "0.3.0"  # docs/catalog-submission.md is the filed 0.3.0 pack: its URLs and versions stay at the version that was filed
PACK_RAW = f"https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v{PACK_V}"
FLOOR_V = "0.3.0"  # the extension still needs only ado >= 0.3.0


def _yaml(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def _json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_workflow_folder_has_readme_license_changelog():
    wf_dir = ROOT / "workflows/ado-lifecycle"
    assert (wf_dir / "LICENSE").read_text(encoding="utf-8") == (ROOT / "LICENSE").read_text(encoding="utf-8")
    readme = (wf_dir / "README.md").read_text(encoding="utf-8")
    assert "story_id" in readme and "integration" in readme
    assert f"{REPO}/blob/main/README.md" in readme
    changelog = (wf_dir / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.findall(r"^## (\d+\.\d+\.\d+)", changelog, re.M) == [V] + [v for v in ("0.3.0", "0.2.0", "0.1.1", "0.1.0") if v != V]  # newest first; V is the current version


def test_workflow_readme_names_every_gate():
    readme = (ROOT / "workflows/ado-lifecycle/README.md").read_text(encoding="utf-8")

    def gates(steps):
        for s in steps:
            if s.get("type") == "gate":
                yield s["id"]
            yield from gates(s.get("steps", []))
    for gate in gates(_yaml("workflows/ado-lifecycle/workflow.yml")["steps"]):
        assert f"`{gate}`" in readme, gate


def test_catalog_dir_is_not_installed_with_the_extension():
    assert "catalog/" in (ROOT / ".extensionignore").read_text(encoding="utf-8").splitlines()


def _check_catalog(path, kind):
    # Fields upstream's catalog loaders read (specify_cli/catalogs.py, workflows/catalog/_domain.py,
    # bundles/component_catalog.py): a JSON object with `<kind>` keyed by id, id == key, a version.
    cat = _json(path)
    assert cat["schema_version"] == "1.0"
    assert cat["catalog_url"] == f"{RAW}/{path}"
    entries = cat[kind]
    assert isinstance(entries, dict) and len(entries) == 1
    (key, entry), = entries.items()
    assert entry["id"] == key and entry["version"] == V
    assert entry.get("verified", False) is False
    assert entry["repository"] == REPO and entry["license"] == "MIT"
    return entry


def test_extension_catalog_matches_manifest():
    entry = _check_catalog("catalog/extensions.json", "extensions")
    m = _yaml("extension.yml")
    ext = m["extension"]
    for field in ("id", "name", "description", "author", "license", "homepage"):
        assert entry[field] == ext[field], field
    assert entry["download_url"] == f"{REPO}/archive/refs/tags/v{V}.zip"
    assert entry["requires"] == m["requires"]
    assert entry["provides"] == {"commands": len(m["provides"]["commands"]), "hooks": len(m["hooks"])}
    assert entry["tags"] == m["tags"]


def test_workflow_catalog_matches_workflow_yml():
    entry = _check_catalog("catalog/workflows.json", "workflows")
    wf = _yaml("workflows/ado-lifecycle/workflow.yml")
    for field in ("id", "name", "description", "author"):
        assert entry[field] == wf["workflow"][field], field
    assert entry["url"] == f"{RAW}/workflows/ado-lifecycle/workflow.yml"
    # A version-pinned install (specify bundle install) rejects a workflow whose
    # `requires` differs from the catalog entry's.
    assert entry["requires"] == wf["requires"]
    assert entry["tags"] == _yaml("bundle/bundle.yml")["tags"]


def test_bundle_docs_name_the_required_catalogs():
    for doc, raw, v in (("README.md", DOC_RAW, DOC_V), ("bundle/README.md", DOC_RAW, DOC_V), ("docs/catalog-submission.md", PACK_RAW, PACK_V)):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert f"specify extension catalog add {raw}/catalog/extensions.json --name ado --install-allowed" in text, doc
        assert f"specify workflow catalog add {raw}/catalog/workflows.json --name ado" in text, doc
        assert f"specify bundle install ./ado-{v}.zip" in text, doc


def test_submission_versions_match_the_real_versions():
    text = (ROOT / "docs/catalog-submission.md").read_text(encoding="utf-8")
    ext = _yaml("extension.yml")
    allowed_specs = {ext["requires"]["speckit_version"]} | {
        t["version"] for t in ext["requires"]["tools"] if "version" in t} | {">=3.9"} | ({f">={DOC_V}"} if RELEASE_PREP_PENDING else set())
    # Only the field tables and JSON blocks: Testing Details are free text the owner fills in
    # (e.g. "Spec Kit 1.2.4").
    fields = [ln for ln in text.splitlines() if ln.startswith("|")]
    fields += re.findall(r"^```json\n(.*?)^```", text, re.M | re.S)
    found = re.findall(r"(>=)?v?(\d+\.\d+(?:\.\d+)?)", "\n".join(fields))
    assert len(found) >= 10
    for spec, version in found:
        if spec:
            assert spec + version in allowed_specs, spec + version
        else:
            assert version == PACK_V, version
    for order in ("## 1. Extension Submission", "## 2. Workflow Submission", "## 3. Bundle Submission"):
        assert order in text
    assert "DRAFT" in text.splitlines()[2]
    assert "TO CONFIRM after publishing" not in text  # Required Component Catalogs is resolved


def test_release_prep_flag_is_a_literal_bool():
    import ast
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    nodes = [n.value for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "RELEASE_PREP_PENDING" for t in n.targets)]
    assert len(nodes) == 1 and isinstance(nodes[0], ast.Constant) and isinstance(nodes[0].value, bool)


def _pack_tool_floor():
    text = (ROOT / "docs/catalog-submission.md").read_text(encoding="utf-8")
    floors = set(re.findall(r'"name": "ado", "version": "(>=[0-9.]+)"', text)) | set(re.findall(r"^- ado \((>=[0-9.]+)\)", text, re.M))
    assert len(floors) == 1, floors
    return floors.pop()


def _manifest_floor():
    return next(t["version"] for t in _yaml("extension.yml")["requires"]["tools"] if t["name"] == "ado")


def _code_versions():
    pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    bundle = _yaml("bundle/bundle.yml")
    return {
        "ado.__version__": V, "pyproject": pyproject, "extension.yml": _yaml("extension.yml")["extension"]["version"],
        "workflow.yml": _yaml("workflows/ado-lifecycle/workflow.yml")["workflow"]["version"], "bundle.yml": bundle["bundle"]["version"],
        "bundle extension pin": bundle["provides"]["extensions"][0]["version"], "bundle workflow pin": bundle["provides"]["workflows"][0]["version"],
        "catalog/extensions.json": _json("catalog/extensions.json")["extensions"]["ado"]["version"],
        "catalog/workflows.json": _json("catalog/workflows.json")["workflows"]["ado-lifecycle"]["version"],
    }


def test_release_prep_state_is_consistent():
    versions, pack = _code_versions(), _pack_tool_floor()
    if RELEASE_PREP_PENDING:
        # Pre-release-prep: everything is still at the previous version, the pack targets the next one.
        assert set(versions.values()) == {PREV_V}, versions
        assert _manifest_floor() == f">={PREV_V}" and pack == f">={DOC_V}"
        assert PREV_V in (ROOT / "bundle/README.md").read_text(encoding="utf-8")  # it says what a build writes today
    else:
        # Release prep done: a version bump to anything but DOC_V, a missed file or a forgotten tool floor fails here.
        assert set(versions.values()) == {DOC_V}, versions
        assert _manifest_floor() == f">={FLOOR_V}" and pack == _manifest_floor()
        assert PREV_V not in (ROOT / "bundle/README.md").read_text(encoding="utf-8")  # drop the "ado-0.2.0.zip today" sentence


def test_release_floors_and_links_agree_everywhere():
    """R-1: one Spec Kit floor (1.2.1) in every manifest, catalog, the pack and doctor; one short description; tag-pinned doc links."""
    from ado import doctor
    floor = ">=1.2.1"
    assert doctor.SPECKIT_FLOOR == floor
    assert _yaml("extension.yml")["requires"]["speckit_version"] == floor
    assert _yaml("workflows/ado-lifecycle/workflow.yml")["requires"]["speckit_version"] == floor
    assert _yaml("bundle/bundle.yml")["requires"]["speckit_version"] == floor
    assert _json("catalog/extensions.json")["extensions"]["ado"]["requires"]["speckit_version"] == floor
    assert _json("catalog/workflows.json")["workflows"]["ado-lifecycle"]["requires"]["speckit_version"] == floor
    pack = (ROOT / "docs/catalog-submission.md").read_text(encoding="utf-8")
    assert pack.count(f"| Required Spec Kit Version | `{floor}` |") == 2 and "0.9.0" not in pack
    desc = _yaml("extension.yml")["extension"]["description"]
    assert len(desc) < 100 and desc == _json("catalog/extensions.json")["extensions"]["ado"]["description"]
    assert pack.count(desc) == 2  # the field table and the proposed catalog entry
    entry = _json("catalog/extensions.json")["extensions"]["ado"]
    assert entry["documentation"] == f"{REPO}/blob/v{V}/README.md" and entry["changelog"] == f"{REPO}/blob/v{V}/CHANGELOG.md"
    assert "/blob/main/" not in pack


def _flat(doc):
    return " ".join((ROOT / doc).read_text(encoding="utf-8").split())


def test_changelog_headings_are_unreleased_or_dated_and_the_dating_step_is_in_the_checklist():
    """R-1: the date is set in the pre-RC step of docs/release-checklist.md; until then (and after) the pins accept both forms."""
    for log in ("CHANGELOG.md", "workflows/ado-lifecycle/CHANGELOG.md"):
        assert len(re.findall("^" + CHANGELOG_030_HEADING.pattern + "$", (ROOT / log).read_text(encoding="utf-8"), re.M)) == 1, log  # a line of its own, once
    assert heading_030_index("x\n## 0.3.0 — unreleased\n") == 2 and heading_030_index("## 0.3.0 — 2026-11-09\n") == 0
    for bad in ("## 0.3.0 — 2026-11-9", "## 0.3.0 — soon", "## 0.3.0 – unreleased", "## 0.3.1 — unreleased"):
        assert not CHANGELOG_030_HEADING.search(bad), bad
    chk = _flat("docs/release-checklist.md")
    assert "**Date the 0.3.0 headings** before you pick `RC`" in chk and "`workflows/ado-lifecycle/CHANGELOG.md`" in chk
    assert "set `updated_at` in `catalog/extensions.json` and `catalog/workflows.json` (the top level and the entry in each, four values)" in chk
    assert "the tests accept both the `unreleased` and the dated heading" in chk


def test_catalog_updated_at_is_a_valid_iso_date_and_follows_a_dated_heading():
    import datetime
    dates = set()
    for path, kind, key in (("catalog/extensions.json", "extensions", "ado"), ("catalog/workflows.json", "workflows", "ado-lifecycle")):
        cat = _json(path)
        entry = cat[kind][key]
        for value in (cat["updated_at"], entry["updated_at"]):
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00Z", value), (path, value)
            dates.add(datetime.datetime.strptime(value, "%Y-%m-%dT00:00:00Z").date())
        assert cat["updated_at"] == entry["updated_at"], path
        assert datetime.datetime.strptime(entry["created_at"], "%Y-%m-%dT00:00:00Z").date() <= datetime.datetime.strptime(entry["updated_at"], "%Y-%m-%dT00:00:00Z").date()
    assert len(dates) == 1, dates  # the four values are set together
    for log in ("CHANGELOG.md", "workflows/ado-lifecycle/CHANGELOG.md"):
        m = re.search(rf"## {re.escape(DOC_V)} — (unreleased|\d{{4}}-\d{{2}}-\d{{2}})(?![\w-])", (ROOT / log).read_text(encoding="utf-8"))
        assert m, f"no dated or unreleased `## {DOC_V}` heading in {log}"
        if m.group(1) != "unreleased":  # after the pre-RC dating step the catalogs carry the same date
            assert m.group(1) == dates.copy().pop().isoformat(), log


def test_init_fallback_version_equals_pyproject():
    """`ado.__version__` reads the installed metadata; the source-tree fallback must still agree with pyproject.toml."""
    fallback = re.search(r'except PackageNotFoundError:.*\n\s+__version__ = "([^"]+)"', (ROOT / "src/ado/__init__.py").read_text(encoding="utf-8"))
    pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert fallback and pyproject and fallback.group(1) == pyproject.group(1) == DOC_V


def test_docs_carry_the_spec_kit_floor_and_the_v0_3_0_install_tags():
    """R-1: every consumer doc names Spec Kit 1.2.1 and the v0.3.0 tag; none still installs the previous tag outside the upgrade history."""
    floors = {"README.md": "Requires Spec Kit 1.2.1 or later",
              "docs/quickstart.md": "[Spec Kit](https://github.com/github/spec-kit) 1.2.1 or later",
              "docs/configuration.md": "`specify --version` is at least `>=1.2.1`",
              "bundle/README.md": "Spec Kit 1.2.1 or later (the supported floor",
              "workflows/ado-lifecycle/README.md": "Spec Kit `>=1.2.1` and a project initialized"}
    for doc, needle in floors.items():
        assert needle in _flat(doc), doc
    assert "`ado --version` — required, `>= 0.3.0`" in _flat("commands/setup.md")
    tags = {"AGENTS.md": ("@v0.3.1", 2), "commands/setup.md": ("@v0.3.1", 1), "docs/ci-github.md": ("v0.3.1", 2), "docs/ci-azure-pipelines.md": ("refs/tags/v0.3.1", 1),
            "VERSIONING.md": ("@v0.3.1", 1), "workflows/ado-lifecycle/README.md": ("@v0.3.1", 1)}
    for doc, (needle, n) in tags.items():
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert text.count(needle) == n, (doc, needle)
        assert "v0.2.0" not in text, doc
    assert "archive/refs/tags/v0.3.1.zip" in (ROOT / "AGENTS.md").read_text(encoding="utf-8")


def test_post_flip_removal_list_names_the_quickstart_paragraph():
    """R-1 M-3, done after the flip: docs/quickstart.md no longer says 0.3.0 is not tagged yet; the removal lists still name the paragraph."""
    assert "0.3.0 is not tagged yet" not in _flat("docs/quickstart.md")
    for doc in ("docs/release-checklist.md", "docs/catalog-submission.md"):
        flat = _flat(doc)
        assert '"0.3.0 is not tagged yet"' in flat and "`docs/quickstart.md`" in flat, doc
