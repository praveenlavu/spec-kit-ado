import collections
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
STRICT_RULES_GATE_BLOCK = ("rule_coverage", "rule_drift")  # added to that floor when rules are enabled
STRICT_REVIEW_BLOCK = ("blocker",)  # review_block_on floor in strict mode
STRICT_DEFAULTS = {"test_command": None, "test_timeout": 300}
RULES_DEFAULTS = {"enabled": False, "types": ["Epic", "Feature"]}  # business rules (R§ config); off = baseline
MAX_REF_CONFIG = 1024 * 1024  # bytes: a config read from a git ref is the PR's neighbour's text; the same cap as the ledger
MAX_APPROVERS = 20
APPROVAL_MODES = ("terminal", "comment")
UNATTENDED_DEFAULTS = {"assume_after_hours": None, "approval_requests": False}  # approval_requests: owner ruling Q4, off by default
TRUST_KEYS = ("approvers", "approval", "unattended")  # read only from the target branch (`trusted`), never from the working tree
GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I | re.A)
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
    "phases", "dod_gate", "mode", "strict", "test_command", "test_timeout", "rules", "enabled", "types",
    "approvers", "approval", "unattended", "assume_after_hours", "approval_requests"})


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
                 mode="advisory", strict=None, rules=None, approvers=None, approval="terminal", unattended=None):
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
        self.rules = {**RULES_DEFAULTS, **(rules or {})}
        self.rules["types"] = list(self.rules["types"])
        self.approvers = list(approvers or [])  # lowercase identity GUIDs; only the target branch's value may be trusted (`trusted`)
        self.approval = approval
        self.unattended = {**UNATTENDED_DEFAULTS, **(unattended or {})}
        self.source_commit = None  # the 40-hex target commit load_from_ref resolved; None for a config read from disk
        self.from_target = False  # True when CI took this config from the PR's target branch (`ado gate --base`)
        self.target_profile_blob = None  # ado.stack.json / ado.kits.lock.json bytes at source_commit (strict.read_target_trust); None: absent
        self.target_lock_blob = None

    def _effective(self, project_list, floor):
        out = list(project_list)
        if self.mode == "strict":
            out += [x for x in floor if x not in out]
        return out

    def effective_gate_block(self):
        """gate.block in force: the project's list, plus the strict floor when mode is strict (and `rule_coverage` and `rule_drift` when rules are enabled)."""
        return self._effective(self.gate["block"], STRICT_GATE_BLOCK + (STRICT_RULES_GATE_BLOCK if self.rules["enabled"] else ()))

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
        for k in ("gate", "stack", "rules", "unattended"):
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
        rules = data.get("rules", {})
        for n, k in enumerate(rules, 1):
            if k not in RULES_DEFAULTS:  # named by position: the key is PR-controlled text
                raise ConfigInvalid(f"{path}: rules entry #{n}: unknown key (valid: {', '.join(RULES_DEFAULTS)})")
        if "enabled" in rules and not isinstance(rules["enabled"], bool):
            raise ConfigInvalid(f"{path}: 'rules.enabled' must be true or false")
        types = rules.get("types")
        if "types" in rules:
            if not (isinstance(types, list) and 1 <= len(types) <= 10
                    and all(isinstance(t, str) and 0 < len(t) <= 64 for t in types)):
                raise ConfigInvalid(f"{path}: 'rules.types' must be a list of 1-10 non-empty strings of at most 64 characters")
            story = data.get("story_type", "User Story").casefold()
            for i, t in enumerate(types):
                if t.casefold() == story:
                    raise ConfigInvalid(f"{path}: rules.types[{i}]: a story type cannot carry rules (it is 'story_type')")
        approvers = cls._validate_trust_keys(data, path)
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
            rules=rules,
            approvers=approvers,
            approval=data.get("approval", "terminal"),
            unattended=data.get("unattended"),
        )

    @staticmethod
    def _validate_trust_keys(data, path):
        """`approvers`, `approval`, `unattended` (P4-7a). Errors name the key and the position, never a value. Returns the approvers:
        lowercased, in order. A duplicate is rejected (not collapsed), as every duplicate id is in this kit."""
        from .client import is_anonymous  # local: client -> config
        approvers = data.get("approvers", [])
        if not (isinstance(approvers, list) and len(approvers) <= MAX_APPROVERS):
            raise ConfigInvalid(f"{path}: 'approvers' must be a list of at most {MAX_APPROVERS} identity ids")
        out = []
        for i, a in enumerate(approvers):
            if not (isinstance(a, str) and GUID_RE.fullmatch(a)):
                raise ConfigInvalid(f"{path}: approvers[{i}]: not an identity id (a GUID: 8-4-4-4-12 hex digits)")
            if is_anonymous(a):
                raise ConfigInvalid(f"{path}: approvers[{i}]: the anonymous identity cannot approve")
            if a.lower() in out:
                raise ConfigInvalid(f"{path}: approvers[{i}]: duplicate of an earlier entry")
            out.append(a.lower())
        if "approval" in data and data["approval"] not in APPROVAL_MODES:
            raise ConfigInvalid(f"{path}: 'approval' must be \"terminal\" or \"comment\"")
        unattended = data.get("unattended", {})
        for n, k in enumerate(unattended, 1):
            if k not in UNATTENDED_DEFAULTS:  # named by position: the key is PR-controlled text
                raise ConfigInvalid(f"{path}: unattended entry #{n}: unknown key (valid: {', '.join(UNATTENDED_DEFAULTS)})")
        hours = unattended.get("assume_after_hours")
        if "assume_after_hours" in unattended and not (isinstance(hours, int) and not isinstance(hours, bool) and 1 <= hours <= 720):
            raise ConfigInvalid(f"{path}: 'unattended.assume_after_hours' must be an integer from 1 to 720")
        if "approval_requests" in unattended and not isinstance(unattended["approval_requests"], bool):
            raise ConfigInvalid(f"{path}: 'unattended.approval_requests' must be true or false")
        return out

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


Trust = collections.namedtuple("Trust", "approvers approval unattended")  # what `trusted` returns: nothing but the three trust keys
_BAD_BASE = re.compile(r"[~^:\s\x00-\x1f\x7f]|@\{|\.\.|^-")  # a revision expression, a range or an option, not a plain ref name


E_NO_TARGET = ("the config file sets approvers, approval or unattended, which are read only from the target branch, "
               "and there is none to read: set origin/HEAD or pass --base")
E_NONROOT_TRUST = ("the config file in use is not the repo-root ado.config.json and sets approvers, approval or unattended: those keys are "
                   "read only from ado.config.json on the default branch; move them there")


def _has_trust_keys(p):
    """Does the regular file `p` (a symlink is followed: it is only searched for the three keys) hold a trust key? Over 1 MiB, unreadable or not a JSON
    object: ConfigInvalid (when it cannot be read, nobody can say it holds none)."""
    try:
        with p.open("rb") as f:
            raw = f.read(MAX_REF_CONFIG + 1)
        data = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_REF_CONFIG else None
    except (OSError, ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise ConfigInvalid("the config file cannot be read as a JSON object of at most 1 MiB, so it cannot be checked for the approval keys")
    return any(k in data for k in TRUST_KEYS)


def _working_tree_has_trust_keys(repo):
    """True when a config file this process would read from the working tree holds a trust key. Read the way `Config.load` finds it (a
    symlink to a regular file is followed: the file is only searched for the three keys). A file that is over 1 MiB, unreadable, or
    not a JSON object raises ConfigInvalid: when it cannot be read, nobody can say it holds none. A path that is no file is absent."""
    paths = [Path(repo) / "ado.config.json"]
    if os.environ.get("ADO_CONFIG"):
        paths.insert(0, Path(os.environ["ADO_CONFIG"]))
    return any(p.is_file() and _has_trust_keys(p) for p in paths)


def _top_config(repo):
    """The git top level's `ado.config.json` (`repo`'s own when git cannot say)."""
    from . import strict
    top = strict._out(str(repo), "rev-parse", "--show-toplevel")
    return Path(top.decode("utf-8", "replace").strip() if top else repo) / "ado.config.json"


def _same_file(a, b):
    """Same file on disk (a symlink, `..`, another spelling or another letter case on a case-insensitive disk included); a missing one is not."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def trusted(repo=".", base=None):
    """`Trust(approvers, approval, unattended)` that may be believed: the TARGET branch's, never the working tree's (the PR edits that).
    `base` (CI: `--base`) or else `strict.default_base(repo)` (`origin/HEAD`). A base is read with `load_from_ref` (a target without a
    config = the defaults; anything unreadable raises ConfigInvalid). A base that is not a plain ref name (a revision expression such as
    `main@{1}` or `origin/main~1`, a range, whitespace, a leading `-`) is refused: it could pick an older target config. With NO base: the
    defaults when the working-tree config sets none of the three keys (a fresh repository behaves as before), else ConfigInvalid, because
    the keys could not be checked against anything. Residual: deleting `origin/HEAD`, or rewriting `refs/remotes/origin/<base>` locally,
    changes what the local CLI trusts; CI fetches fresh."""
    from . import strict  # local: strict -> gate -> questions -> auth -> secretstore -> config
    # only the git top level's ado.config.json is read from the target: trust keys in the file `Config.load` reads instead would be dropped silently
    used = next((p for p in (Path(os.environ.get("ADO_CONFIG") or "\0"), Path(repo) / "ado.config.json") if p.is_file()), None)
    if used is not None and not _same_file(used, _top_config(repo)) and _has_trust_keys(used):
        raise ConfigInvalid(E_NONROOT_TRUST)
    ref = base or strict.default_base(str(repo))
    if ref:
        if not isinstance(ref, str) or _BAD_BASE.search(ref):
            raise ConfigInvalid("the base is not a plain branch or ref name (no revision expressions, ranges, spaces or leading dash)")
        cfg = Config.load_from_ref(ref, repo=str(repo))
    elif _working_tree_has_trust_keys(repo):
        raise ConfigInvalid(E_NO_TARGET)
    else:
        cfg = None
    cfg = cfg or Config("", "")  # no config at the target (or no base): the defaults
    return Trust(list(cfg.approvers), cfg.approval, dict(cfg.unattended))
