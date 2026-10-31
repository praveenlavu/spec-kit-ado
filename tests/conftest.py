import os
import subprocess

import pytest

from ado import dod
from ado.config import Config


class FakeClient:
    """Records calls; returns queued responses (dicts) in order."""

    def __init__(self, config, responses=None):
        self.config = config
        self.calls = []
        self._responses = list(responses or [])

    def request(self, method, path, body=None, content_type="application/json", raw=False):
        self.calls.append(
            {"method": method, "path": path, "body": body, "content_type": content_type}
        )
        return self._responses.pop(0) if self._responses else {}

    def get(self, path):
        return self.request("GET", path)

    def get_bytes(self, url, max_bytes):
        self.calls.append({"method": "GET_BYTES", "path": url, "max_bytes": max_bytes})
        out = self._responses.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out

    def post(self, path, body, **kw):
        return self.request("POST", path, body, **kw)

    def patch(self, path, body, **kw):
        return self.request("PATCH", path, body, **kw)


@pytest.fixture
def cfg():
    return Config(organization="contoso", project="Fabrikam")


# ---- real temp git repos for the review_block_on checks (F11) ----
def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


def write_files(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def commit(root, msg, files=None):
    write_files(root, files or {})
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "--short", "HEAD")


def review_md(sha, blocks="no", superseded=False, blocker=0, major=0):
    tail = " (superseded)" if superseded else ""
    verdict = "yes — `review_block_on` = [blocker] and 1 such finding(s) remain" if blocks == "yes" else "no"
    return (f"### Review — 2026-10-28 — `main...feat` @ `{sha}`{tail}\n\n"
            f"- **Confirmed by severity:** blocker {blocker} · major {major} · minor 0\n"
            f"- **Blocks PR:** {verdict}\n\n")


def ledger_md(*blocks, spec_dir="specs/001-x"):
    return (f"# DoD\n- **Spec dir:** {spec_dir}\n\n{dod.SECTION2}\n\n## 3. AC\n\n"
            "## 4. Gate & review log  (appended by speckit.ado.gate and speckit.ado.review)\n\n"
            + "".join(blocks) + "## 5. Checklist\n- [ ] x\n")


@pytest.fixture
def git_env(monkeypatch):
    """Isolate git from the user's config (signing, hooks) and give commits a fixed identity."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{k}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{k}_EMAIL", "test@example.invalid")


@pytest.fixture
def real_detect():
    """Opt-in marker (see `_stub_detect`): the test runs the real stack detection of the strict `stack-unprofiled` rule."""


@pytest.fixture(autouse=True)
def _stub_detect(request, monkeypatch):
    """Most strict tests run in a plain temporary directory that is no git repository; `stack-unprofiled` (P2-5b) would call that a
    finding. Those tests are about other rules, so detection finds nothing there; a test of the rule asks for `real_detect`."""
    if "real_detect" not in request.fixturenames:
        monkeypatch.setattr("ado.strict._detected", lambda repo, commit: {"outcome": "unknown", "stacks": [], "domain_hints": []})
