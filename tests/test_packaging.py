"""The package metadata PyPI shows (Task F1). tomllib is not in Python 3.9, so the file is read as text."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = (ROOT / "pyproject.toml").read_text()


def test_pypi_metadata_present():
    for key in ("readme", "authors", "keywords", "classifiers"):
        assert re.search(rf"^{key}\s*=", PYPROJECT, re.M), key
    assert "[project.urls]" in PYPROJECT
    for name in ("Homepage", "Source", "Issues", "Changelog"):
        assert re.search(rf'^{name}\s*=\s*"https://github.com/praveenlavu/spec-kit-ado', PYPROJECT, re.M), name


def test_author_is_the_handle_only():
    m = re.search(r"^authors\s*=\s*(.+)$", PYPROJECT, re.M)
    assert m, "authors missing"
    assert "@" not in m.group(1) and "email" not in m.group(1)


def test_readme_images_are_absolute_urls():
    # PyPI cannot resolve repo-relative image paths.
    images = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", (ROOT / "README.md").read_text())
    assert images, "the README has no images to check"
    for url in images:
        assert url.startswith("https://"), url
