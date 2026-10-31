import json
import os
import re
import unicodedata
from pathlib import Path

DEFAULT_API_VERSION = "7.1"
ORG_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")  # organization goes into a URL path
REVIEW_SEVERITIES = ("blocker", "major", "minor")  # review_block_on entries (case-sensitive)
MODES = ("advisory", "strict")
STRICT_GATE_BLOCK = ("ac_traceability", "assertion", "open_questions", "commits_without_story")  # gate.block floor in strict mode
STRICT_REVIEW_BLOCK = ("blocker",)  # review_block_on floor in strict mode
STRICT_DEFAULTS = {"test_command": None, "test_timeout": 300}
MAX_REF_CONFIG = 1024 * 1024  # bytes: a config read from a git ref is the PR's neighbour's text; the same cap as the ledger
REF_LABEL = "ado.config.json at the target branch"  # names the file in messages, never the ref (it is a CLI value)


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
    "phases", "dod_gate", "mode", "strict", "test_command", "test_timeout"})


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
                 story_type="User Story", removed_state="Removed", branch_prefix="feature/",
                 mode="advisory", strict=None):
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
        self.mode = mode
        self.strict = {**STRICT_DEFAULTS, **(strict or {})}
        self.source_commit = None  # the 40-hex target commit load_from_ref resolved; None for a config read from disk
        self.from_target = False  # True when CI took this config from the PR's target branch (`ado gate --base`)

    def _effective(self, project_list, floor):
        out = list(project_list)
        if self.mode == "strict":
            out += [x for x in floor if x not in out]
        return out

    def effective_gate_block(self):
        """gate.block in force: the project's list, plus the strict floor when mode is strict."""
        return self._effective(self.gate["block"], STRICT_GATE_BLOCK)

    def effective_review_block_on(self):
        """review_block_on in force: the project's list, plus the strict floor when mode is strict."""
        return self._effective(self.review_block_on, STRICT_REVIEW_BLOCK)

    @classmethod
    def _from_data(cls, data, path):
        """The one validator behind `load` and `load_from_ref`; `path` only labels the messages (the PR chooses the content: never the text)."""
        try:
            return cls._validate(data, path)
        except RecursionError:  # a value nested too deeply for the validator
            raise ConfigInvalid(f"{path}: nested too deeply") from None

    @classmethod
    def _validate(cls, data, path):
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
        for k in ("story_type", "removed_state", "done_state"):
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
        if "mode" in data and data["mode"] not in MODES:
            raise ConfigInvalid(f"{path}: 'mode' must be \"advisory\" or \"strict\"")
        strict = data.get("strict", {})
        if not isinstance(strict, dict):
            raise ConfigInvalid(f"{path}: 'strict' must be a JSON object")
        for n, k in enumerate(strict, 1):
            if k not in STRICT_DEFAULTS:  # named by position: the key is PR-controlled text
                raise ConfigInvalid(f"{path}: strict entry #{n}: unknown key "
                                  f"(valid: {', '.join(STRICT_DEFAULTS)})")
        cmd = strict.get("test_command")
        if "test_command" in strict and not (isinstance(cmd, str) and 0 < len(cmd) <= 500):
            raise ConfigInvalid(f"{path}: 'strict.test_command' must be a string of 1-500 characters")
        tt = strict.get("test_timeout")
        if "test_timeout" in strict and not (isinstance(tt, int) and not isinstance(tt, bool) and 1 <= tt <= 3600):
            raise ConfigInvalid(f"{path}: 'strict.test_timeout' must be an integer from 1 to 3600")
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
            mode=data.get("mode", "advisory"),
            strict=strict,
        )

    @classmethod
    def load_from_ref(cls, ref, repo="."):
        """The config at `ref` (the PR's target branch), read with git and checked by the same validator as `load`: a Config, or None
        when the repository has no `ado.config.json` at that ref. Anything else (a ref that is no commit, git missing or failing, a
        non-regular or over 1 MiB file, bad content) raises ConfigInvalid with text that never echoes the ref: CI fails closed.
        `ref` is resolved as strict._resolve_base does: refs/remotes/<ref>, refs/heads/<ref>, a full commit id; never a tag."""
        from . import strict  # local: strict -> gate -> questions -> auth -> secretstore -> config

        def fail(why):
            return ConfigInvalid(f"{REF_LABEL}: {why}")
        commit = strict._resolve_base(repo, ref) if isinstance(ref, str) else ""
        if not commit:
            raise fail("the base ref is not a commit in this repository (or git could not be run); fetch it (fetch-depth: 0)")
        raw = strict._blob_at(repo, commit, "ado.config.json", MAX_REF_CONFIG, REF_LABEL)
        if raw is None:
            return None  # the path does not exist at that commit
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as e:  # includes UnicodeDecodeError
            raise fail(f"invalid JSON: {e}")
        except RecursionError:
            raise fail("nested too deeply") from None
        cfg = cls._from_data(data, REF_LABEL)
        cfg.source_commit = commit
        return cfg

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
                except RecursionError:
                    raise ConfigInvalid(f"{path}: nested too deeply") from None
                return cls._from_data(data, path)
        raise ConfigError(
            "No ADO config found. Run `ado setup` to create ado.config.json here, "
            "or point $ADO_CONFIG at a config file."
        )
