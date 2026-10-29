import json
import os
import re
import unicodedata
from pathlib import Path

DEFAULT_API_VERSION = "7.1"
ORG_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")  # organization goes into a URL path
REVIEW_SEVERITIES = ("blocker", "major", "minor")  # review_block_on entries (case-sensitive)


BRANCH_PREFIX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def valid_org(org):
    return isinstance(org, str) and ORG_RE.fullmatch(org) is not None


def valid_branch_prefix(prefix):
    """Empty, or a git-ref-safe start of a branch name (`<prefix>ab<id>-<slug>` must stay a valid ref)."""
    return isinstance(prefix, str) and (prefix == "" or (
        BRANCH_PREFIX_RE.fullmatch(prefix) is not None
        and not any(bad in prefix for bad in ("..", "//", "/.", ".lock/"))))


_HARMLESS_FORMAT = "\u200c\u200d\u200e\u200f"  # ZWNJ, ZWJ, LRM, RLM: Persian/Indic names, emoji sequences, mixed-RTL text


_FIELDS = frozenset({  # fixed config field names: safe to print
    "organization", "project", "api_version", "dod_path", "gate", "tests", "block", "attachments_allow", "review_block_on",
    "done_state", "task_type", "repo_host", "repository", "story_type", "removed_state", "branch_prefix", "stack", "kit",
    "phases", "dod_gate"})


def _has_control(s):
    """Control (Cc), line/paragraph separators (Zl, Zp), surrogates, private use and format characters, except the
    four joiner/direction marks above. Cn (unassigned) is deliberately accepted: it depends on the Unicode DB version."""
    return any(unicodedata.category(c) in ("Cc", "Zl", "Zp", "Cs", "Co")
               or (unicodedata.category(c) == "Cf" and c not in _HARMLESS_FORMAT) for c in s)


def reject_control_chars(node, where, path):
    """The config comes from the PR in CI, so no string (value or key, any depth) may carry a control character
    (it could start a new CI log line). `where` is the key path ("" at the top). Names the key path, never the offending text."""
    if isinstance(node, str):
        if _has_control(node):
            raise ConfigInvalid(f"{path}: {where} contains a control character")
    elif isinstance(node, dict):
        for n, (k, v) in enumerate(node.items(), 1):
            if _has_control(k):
                raise ConfigInvalid(f"{path}: {where or 'top level'}: a key contains a control character")
            # a key that is not one of the fixed field names is user text: name it by position
            label = k if k in _FIELDS else f"<entry #{n}>"
            reject_control_chars(v, f"{where}.{label}" if where else label, path)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            reject_control_chars(v, f"{where}[{i}]", path)


class ConfigError(Exception):
    """No usable ado.config.json found."""


class ConfigInvalid(ConfigError):
    """A config file is present but invalid (explicit misconfiguration; `ado gate` exits 3, CI fails)."""


class Config:
    def __init__(self, organization, project, api_version=DEFAULT_API_VERSION,
                 dod_path="dod", gate=None, attachments_allow=None, review_block_on=None,
                 done_state="Done", task_type="Task",
                 repo_host="github", repository="",
                 story_type="User Story", removed_state="Removed", branch_prefix="feature/"):
        self.organization = organization
        self.project = project
        self.api_version = api_version
        self.dod_path = dod_path
        self.gate = {"tests": {}, "block": [], **(gate or {})}  # partial gate keeps defaults
        self.attachments_allow = (attachments_allow if attachments_allow is not None
                                  else [".md", ".svg", ".txt"])
        self.review_block_on = review_block_on or []
        self.done_state = done_state
        self.task_type = task_type
        self.repo_host = repo_host
        self.repository = repository
        self.story_type = story_type
        self.removed_state = removed_state
        self.branch_prefix = branch_prefix

    @classmethod
    def load(cls, start_dir=None):
        candidates = []
        env_path = os.environ.get("ADO_CONFIG")
        if env_path:
            candidates.append(Path(env_path))
        candidates.append(Path(start_dir or os.getcwd()) / "ado.config.json")
        for path in candidates:
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except OSError as e:
                    raise ConfigInvalid(f"{path}: cannot read ({e})")
                except ValueError as e:  # includes UnicodeDecodeError
                    raise ConfigInvalid(f"{path}: invalid JSON: {e}")
                if not isinstance(data, dict):
                    raise ConfigInvalid(f"{path}: top level must be a JSON object")
                reject_control_chars(data, "", path)
                missing = [k for k in ("organization", "project") if not data.get(k)]
                if missing:
                    raise ConfigInvalid(f"{path}: missing required key(s): {', '.join(missing)}")
                if "dod_gate" in data:
                    raise ConfigInvalid(f"{path}: `dod_gate` was renamed to `gate` in 0.1.1; "
                                      "move `dod_gate.block` to `gate.block`")
                for k in ("gate", "stack"):
                    if k in data and not isinstance(data[k], dict):
                        raise ConfigInvalid(f"{path}: '{k}' must be a JSON object")
                org = data["organization"]
                if not (valid_org(org)):
                    raise ConfigInvalid(f"{path}: 'organization' must match {ORG_RE.pattern}")
                gate = data.get("gate", {})
                if "tests" in gate and not isinstance(gate["tests"], dict):
                    raise ConfigInvalid(f"{path}: 'gate.tests' must be a JSON object")
                from .gate import GATE_CHECKS, valid_glob  # local: gate -> questions -> auth -> secretstore -> config
                for n, (glob, rx) in enumerate(gate.get("tests", {}).items(), 1):
                    # entries are named by position: the glob is PR-controlled text and must not reach the CI log
                    if not (isinstance(rx, str) and 0 < len(rx) <= 500):
                        raise ConfigInvalid(f"{path}: 'gate.tests' entry #{n}: the value must be a regex string of 1-500 characters")
                    if not valid_glob(glob):
                        raise ConfigInvalid(f"{path}: 'gate.tests' entry #{n}: the glob must be 1-200 characters "
                                          "with balanced '[...]' classes")
                    try:
                        re.compile(rx)
                    except re.error:
                        raise ConfigInvalid(f"{path}: 'gate.tests' entry #{n}: the regex is invalid") from None
                dod_path = data.get("dod_path", "dod")
                if not (isinstance(dod_path, str) and dod_path):
                    raise ConfigInvalid(f"{path}: 'dod_path' must be a non-empty string")
                block = gate.get("block", [])
                if not (isinstance(block, list) and all(isinstance(b, str) for b in block)):
                    raise ConfigInvalid(f"{path}: 'gate.block' must be a list of strings")
                for i, b in enumerate(block):
                    if b not in GATE_CHECKS:
                        raise ConfigInvalid(f"{path}: gate.block[{i}]: unknown check "
                                          f"(valid: {', '.join(GATE_CHECKS)})")
                for k in ("story_type", "removed_state"):
                    if k in data and not (isinstance(data[k], str) and data[k]):
                        raise ConfigInvalid(f"{path}: '{k}' must be a non-empty string")
                if not valid_branch_prefix(data.get("branch_prefix", "feature/")):
                    raise ConfigInvalid(f"{path}: 'branch_prefix' must be empty or a git-ref-safe string "
                                      "(letters, digits, '.', '_', '-', '/'; no '..', '//', '/.', '.lock/')")
                allow = data.get("attachments_allow")
                if allow is not None and not (
                        isinstance(allow, list) and all(isinstance(a, str) for a in allow)):
                    raise ConfigInvalid(f"{path}: 'attachments_allow' must be a list of strings")
                rbo = data.get("review_block_on", [])
                if not (isinstance(rbo, list) and all(isinstance(r, str) for r in rbo)):
                    raise ConfigInvalid(f"{path}: 'review_block_on' must be a list of strings")
                for i, r in enumerate(rbo):
                    if r not in REVIEW_SEVERITIES:
                        raise ConfigInvalid(f"{path}: review_block_on[{i}]: unknown severity "
                                          f"(valid: {', '.join(REVIEW_SEVERITIES)})")
                return cls(
                    organization=data["organization"],
                    project=data["project"],
                    api_version=data.get("api_version", DEFAULT_API_VERSION),
                    dod_path=dod_path,
                    gate=data.get("gate"),
                    attachments_allow=allow,
                    review_block_on=rbo,
                    done_state=data.get("done_state", "Done"),
                    task_type=data.get("task_type", "Task"),
                    repo_host=data.get("repo_host", "github"),
                    repository=data.get("repository", ""),
                    story_type=data.get("story_type", "User Story"),
                    removed_state=data.get("removed_state", "Removed"),
                    branch_prefix=data.get("branch_prefix", "feature/"),
                )
        raise ConfigError(
            "No ADO config found. Run `ado setup` to create ado.config.json here, "
            "or point $ADO_CONFIG at a config file."
        )
