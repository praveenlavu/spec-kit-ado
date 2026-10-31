import copy
import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from ado import dod, stack
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


def profile(**over):
    p = {"schema": 1, "version": 2, "status": "draft",
         "stacks": [{"id": "python", "root": ".",
                     "detected": {"confidence": "high", "evidence": ["pyproject.toml"]},
                     "commands": {"test": {"run": "PYTHONPATH=src python3 -m pytest -q", "source": "S1"},
                                  "lint": {"run": "ruff check .", "source": "unverified"},
                                  "org_only": [{"run": "sf apex run test --wait 10", "source": "S2"}]},
                     "conventions": [{"rule": "Test files are named test_*.py", "source": "S1"},
                                     {"rule": "Keep modules small", "source": "unverified"}],
                     "dod": [{"item": "No new lint findings", "source": "S1"}]}],
         "sources": {"S1": {"kind": "official", "url": "https://docs.pytest.org/en/stable/", "retrieved": "2026-01-15",
                            "evidence": "Run tests with python -m pytest."},
                     "S2": {"kind": "repo", "path": "package.json", "retrieved": "2026-01-15", "evidence": "scripts.test"}}}
    p.update(over)
    return p


def approve(p, **over):
    """`p` as a human's approval records it: status approved and the hash of everything but `approval`."""
    p = copy.deepcopy(p)
    p["status"] = "approved"
    p["approval"] = {"by": "tech lead", "date": "2026-01-16", "sha256": "0" * 64}
    p["approval"]["sha256"] = stack.profile_hash(p)
    p["approval"].update(over)
    return p


# ---- real temp git repos for the review_block_on checks (F11) ----
_IDENTITY = {f"GIT_{k}_{f}": v for k in ("AUTHOR", "COMMITTER") for f, v in (("NAME", "Test"), ("EMAIL", "test@example.invalid"))}


def git(repo, *a):
    # a commit needs an identity even in a bare container (no global git config); an identity already in the environment wins
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True, env={**_IDENTITY, **os.environ}).stdout.strip()


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


# ---- the typed approval code of `ado stack approve --apply` and `ado kits pin --apply` (P2-16b) ----
APPROVAL_CODE = re.compile(r"approval code: ([0-9a-f]{12})")
_SRC = str(Path(__file__).resolve().parent.parent / "src")


def shown_code(screen):
    """The approval code on a screen (what a human would read and type)."""
    found = APPROVAL_CODE.findall(screen)
    assert found, "no approval code on the screen"
    return found[-1]


class TypedStdin:
    """An in-process terminal stdin (`isatty` is True) whose `readline` answers like a human who reads the screen: by default it types the
    code printed so far on `sys.stdout` (pytest's capture, read without consuming it). `answer` is returned as the line instead
    (a newline alone is an empty line, `""` end of input, an exception class is raised). `before(code)` runs after the prompt and before the answer:
    the hook point for changing a file between the prompt and the typed code. `lines` counts the readline calls."""

    def __init__(self, answer=None, before=None):
        self.answer, self.before, self.lines = answer, before, 0

    def isatty(self):
        return True

    def readline(self, size=-1):
        self.lines += 1
        if self.before is not None:
            self.before(shown_code(sys.stdout.getvalue()))
        if isinstance(self.answer, type) and issubclass(self.answer, BaseException):
            raise self.answer
        return self.answer if self.answer is not None else shown_code(sys.stdout.getvalue()) + "\n"


def terminal(monkeypatch, answer=None, before=None):
    """stdin is a `TypedStdin` and stdout a terminal (use with capsys); returns the stdin."""
    stdin = TypedStdin(answer, before)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    return stdin


def run_in_pty(args, cwd, answer=None, before=None, stdout_pty=True, stdin_pty=True, interrupt=False, early=None, timeout=60):
    """`python -m ado ARGS` as a real process with a pty on stdin (and on stdout unless `stdout_pty` is False). When the screen shows
    `approval code: <12 hex>` it calls `before(code)` (change a file between the prompt and the typed code), then types `answer` (default:
    the code; `""` sends Ctrl-D, end of input) or, with `interrupt`, sends SIGINT. `early` is typed right after the start, before any prompt. Returns `(returncode, screen, stderr)`; `screen` has
    CRLF turned into LF. Without a code on the screen nothing is typed."""
    env = {**os.environ, "PYTHONPATH": _SRC}
    env.pop("ADO_CONFIG", None)
    master, slave = pty.openpty()
    proc = subprocess.Popen([sys.executable, "-m", "ado", *args], cwd=cwd, env=env, stdin=slave if stdin_pty else subprocess.DEVNULL,
                            stdout=slave if stdout_pty else subprocess.PIPE, stderr=subprocess.PIPE)
    os.close(slave)
    if early is not None:
        os.write(master, early.encode() + b"\n")
    out, typed = b"", False
    try:
        while True:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            elif proc.poll() is not None:
                break
            if not typed and APPROVAL_CODE.search(out.decode(errors="replace")):
                typed, code = True, shown_code(out.decode(errors="replace"))
                if before is not None:
                    before(code)
                if interrupt:
                    proc.send_signal(signal.SIGINT)
                else:
                    os.write(master, b"\x04" if answer == "" else (code if answer is None else answer).encode() + b"\n")
        piped, err = proc.communicate(timeout=timeout)
    finally:
        os.close(master)
        if proc.poll() is None:
            proc.kill()
    return proc.returncode, (out if stdout_pty else piped).decode().replace("\r\n", "\n"), err.decode()


# ---- a REAL Spec Kit repository (P2-17a): `specify init`, `specify extension add --dev` for the toolkit and for a fictional kit, committed ----
# Hand-made fixtures held only `.specify/extensions/<kit>/`; a real repo also commits `.specify/init-options.json`, `integration.json`,
# `extensions.yml`, `.gitignore`, `integrations/`, `memory/`, `scripts/`, `templates/`, `workflows/` and the `.claude/` agent copies,
# and tracks the toolkit's own `.specify/extensions/ado/`. Built once per session (about a second), copied per test.
SPECKIT_KIT = "contoso-kit"
_EXT_PARTS = ("extension.yml", "commands", "rules", "docs", "README.md", "CHANGELOG.md", "LICENSE", "AGENTS.md", "ado-config.template.json")
_KIT_MANIFEST = f"""schema_version: "1.0"

extension:
  id: "{SPECKIT_KIT}"
  name: "Contoso kit (fictional)"
  version: "0.1.0"
  description: "A fictional one-command kit."
  author: "Dana Fictional"
  repository: "https://example.invalid/contoso/{SPECKIT_KIT}"
  license: "MIT"

requires:
  speckit_version: ">=0.9.0"

provides:
  commands:
    - name: "speckit.{SPECKIT_KIT}.hello"
      file: "commands/hello.md"
      description: "Print a short reminder."
"""


def require_specify():
    """Path of the `specify` CLI. Missing: skip locally, but fail under CI (GitHub sets `CI=true`) so the real-repo tests cannot vanish silently."""
    specify = shutil.which("specify")
    if specify is None:
        (pytest.fail if os.environ.get("CI") else pytest.skip)("the specify CLI is not installed")
    return specify


@pytest.fixture(scope="session")
def speckit_template(tmp_path_factory):
    """Path of a committed Spec Kit repository on `main`: strict `ado.config.json`, an approved `ado.stack.json`, and an
    `ado.kits.lock.json` pinning `contoso-kit` to its committed files (the toolkit's `ado` is tracked and unpinned, as in a normal install).
    Skipped when the `specify` CLI is not on PATH."""
    specify = require_specify()
    from ado import kits  # noqa: PLC0415
    root = tmp_path_factory.mktemp("speckit-template")
    repo, kit_src, ext_src = root / "repo", root / "kit-src", root / "ext-src"
    for d in (repo, kit_src / "commands", ext_src):
        d.mkdir(parents=True)
    (kit_src / "extension.yml").write_text(_KIT_MANIFEST)
    (kit_src / "commands" / "hello.md").write_text("---\ndescription: Print a short reminder.\n---\n\nPrint a short reminder.\n")
    here = Path(__file__).resolve().parent.parent
    for part in _EXT_PARTS:
        (shutil.copytree if (here / part).is_dir() else shutil.copy)(here / part, ext_src / part)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "NO_COLOR": "1",
           **{f"GIT_{k}_{f}": v for k in ("AUTHOR", "COMMITTER") for f, v in (("NAME", "Test"), ("EMAIL", "test@example.invalid"))}}

    def run(*cmd):
        subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True, timeout=120)
    run("git", "init", "-q", "-b", "main")
    run(specify, "init", "--here", "--force", "--non-interactive", "--integration", "claude", "--script", "sh")
    run(specify, "extension", "add", "--dev", str(ext_src))
    run(specify, "extension", "add", "--dev", str(kit_src))
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "specify init and extensions")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()
    pin = {"version": "0.1.0", "source": None, "catalog_sha256": None, "dir_digest": kits.dir_digest_at(str(repo), sha, SPECKIT_KIT), "agents_sha256": "a" * 64}
    (repo / "ado.kits.lock.json").write_text(json.dumps({"schema": 1, "kits": {SPECKIT_KIT: pin}}))
    (repo / "ado.stack.json").write_text(json.dumps(approve(profile()), indent=1))
    (repo / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", "mode": "strict",
                                                      "strict": {"test_command": "PYTHONPATH=src python3 -m pytest -q"}}))
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "adopt strict mode")
    return repo


@pytest.fixture
def speckit_repo(speckit_template, tmp_path, git_env):
    """A private copy of `speckit_template` (a git repository on `main`)."""
    repo = tmp_path / "repo"
    shutil.copytree(speckit_template, repo, symlinks=True)
    return repo
