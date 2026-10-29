"""Catalog readiness: workflow folder, self-hosted catalogs, submission draft (N7)."""
import json
import re
from pathlib import Path

import yaml

import ado

ROOT = Path(__file__).resolve().parent.parent
V = ado.__version__
REPO = "https://github.com/praveenlavu/spec-kit-ado"
RAW = f"https://raw.githubusercontent.com/praveenlavu/spec-kit-ado/v{V}"


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
    assert re.findall(r"^## (\d+\.\d+\.\d+)", changelog, re.M) == [V, "0.1.1", "0.1.0"]


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
    for doc in ("README.md", "bundle/README.md", "docs/catalog-submission.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert f"specify extension catalog add {RAW}/catalog/extensions.json --name ado --install-allowed" in text, doc
        assert f"specify workflow catalog add {RAW}/catalog/workflows.json --name ado" in text, doc
        assert f"specify bundle install ./ado-{V}.zip" in text, doc


def test_submission_versions_match_the_real_versions():
    text = (ROOT / "docs/catalog-submission.md").read_text(encoding="utf-8")
    ext = _yaml("extension.yml")
    allowed_specs = {ext["requires"]["speckit_version"]} | {
        t["version"] for t in ext["requires"]["tools"] if "version" in t} | {">=3.9"}
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
            assert version == V, version
    for order in ("## 1. Extension Submission", "## 2. Workflow Submission", "## 3. Bundle Submission"):
        assert order in text
    assert "DRAFT" in text.splitlines()[2]
    assert "TO CONFIRM after publishing" not in text  # Required Component Catalogs is resolved
