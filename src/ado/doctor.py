"""`ado doctor`: what is wrong with this setup, in rows `{id, status, fix}`. Status is `ok`, `warn`, `fail` or `unknown`; `unknown` means a
probe could not tell, and a probe that cannot tell never says `ok`. Nothing here writes: local reads, `git --version`, `git rev-parse` and
`specify --version` (scrubbed environment, a timeout, only a parsed version is ever kept from their output) and three ADO calls, all GETs through
`AdoClient`: `connectionData`, then (the `bypass` row, only once the token identity is known) the project and its permission answer. Row texts are fixed: ids, counts and numeric versions only, never a token, a name, an email, a config
value or tool output. Config errors name the key by position (the validator's own texts). The scope probes and `--probe-item` belong to a
later task: `scopes` stays empty and `probe_item` is accepted and ignored. Process help, not a security boundary: a hostile checkout can
shape the local rows it controls."""
import json
import os
import re
import subprocess

from . import __version__, approvals, auth, comments, config, kits, stack, strict
from .client import AdoClient, AdoError, is_anonymous
from .config import Config, ConfigError, ConfigInvalid

GIT_FLOOR = (2, 24)  # `--end-of-options`
SPECKIT_FLOOR = ">=0.9.0"  # used when the installed extension.yml cannot say; a test pins it to the repo's extension.yml
TIMEOUT = 10  # seconds per git/specify call
MAX_MANIFEST = 256 * 1024
EXT_PATH = ".specify/extensions/ado/extension.yml"
WF_PATH = ".specify/workflows/ado-lifecycle/workflow.yml"
CONNECTION = "/_apis/connectionData?api-version=7.1-preview"
ROW_IDS = ("git", "specify", "shallow", "config", "config-target", "token", "identity", "extension", "workflow", "approvers", "bypass", "stack", "kits")
_VER = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_SPECIFY = re.compile(r"^specify(?:[ -]cli)?[ \t]+v?(\d+)\.(\d+)\.(\d+)", re.M | re.I)
_GIT = re.compile(r"^git version (\d+)\.(\d+)(?:\.(\d+))?", re.M)
_MANIFEST_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,40}")


class _Missing(Exception):
    """The tool is not installed."""


class _Unusable(Exception):
    """The tool ran but gave no usable answer (timeout, crash, non-zero exit)."""


def _run(argv, cwd=None, git=False):
    """stdout (text) of a successful run: scrubbed environment (no token or secret-looking variable), no stdin, a timeout."""
    home = os.environ.get("HOME", "/nonexistent")
    env = strict._git_env(os.environ, home) if git else {**strict.scrubbed_env(os.environ, home), "NO_COLOR": "1"}
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=TIMEOUT, check=False)
    except FileNotFoundError:
        raise _Missing from None
    except (OSError, subprocess.SubprocessError):
        raise _Unusable from None
    if r.returncode:
        raise _Unusable
    return r.stdout.decode("utf-8", "replace")


def _vers(m):
    return tuple(int(g or 0) for g in m.groups())


def _dotted(v):
    return ".".join(str(n) for n in v)


def _git_row(repo):
    try:
        m = _GIT.search(_run(["git", "--version"], git=True))
    except _Missing:
        return "fail", "git is not installed or not on PATH: install git 2.24 or newer"
    except _Unusable:
        return "unknown", "`git --version` gave no usable answer"
    if not m:
        return "unknown", "`git --version` gave an answer that could not be read"
    found = _vers(m)[:2]
    if found < GIT_FLOOR:
        return "fail", f"git {_dotted(found)} is older than {_dotted(GIT_FLOOR)}: upgrade git"
    return "ok", ""


def _shallow_row(repo):
    try:
        said = _run(["git", "-C", str(repo), "rev-parse", "--is-shallow-repository"], git=True).strip()
    except (_Missing, _Unusable):
        return "unknown", "git could not tell whether this is a shallow clone (not a git repository?)"
    if said == "true":
        return "fail", "this clone is shallow, so base and merge-base checks fail: run `git fetch --unshallow`"
    return ("ok", "") if said == "false" else ("unknown", "git gave an answer about the clone depth that could not be read")


def _manifest(repo, rel, section, key):
    """(status, value): a `  key: value` line inside the top-level `section:` of a manifest read as confined as a kit file
    (no symlink, 256 KiB). status is `ok`, `absent` or `unreadable`. A line scan, not YAML (stdlib only)."""
    try:
        raw = kits._read_regular(repo, rel, MAX_MANIFEST, missing_ok=True)
    except kits.KitError:
        return "unreadable", None
    if raw is None:
        return "absent", None
    inside = False
    for line in raw.decode("utf-8", "replace").splitlines():
        top = re.match(r"^([A-Za-z_]+):\s*(?:#.*)?$", line)
        if top or re.match(r"^\S", line):
            inside = bool(top) and top.group(1) == section
        elif inside and (m := re.match(rf"^  {key}:\s*[\"']?([^\"'#]+?)[\"']?\s*(?:#.*)?$", line)):
            return "ok", m.group(1)
    return "unreadable", None


def _specify_row(repo):
    status, floor = _manifest(repo, EXT_PATH, "requires", "speckit_version")
    wanted = [SPECKIT_FLOOR] + ([floor] if status == "ok" else [])
    needs = [re.fullmatch(r">=\s*(\d+\.\d+\.\d+)", w) for w in wanted]
    if not all(needs):
        return "unknown", "the Spec Kit version the extension requires is written in a form doctor cannot read"
    need = max(_vers(_VER.fullmatch(n.group(1))) for n in needs)  # a checkout can raise the built-in floor, never lower it
    try:
        m = _SPECIFY.search(_run(["specify", "--version"]))
    except _Missing:
        return "warn", "the `specify` CLI is not on PATH: install Spec Kit (`uv tool install specify-cli`) to use the lifecycle workflow"
    except _Unusable:
        return "unknown", "`specify --version` gave no usable answer"
    if not m:
        return "unknown", "`specify --version` gave an answer that could not be read"
    if _vers(m) < need:
        return "fail", f"specify {_dotted(_vers(m))} is older than {_dotted(need)}, which the extension requires: upgrade Spec Kit"
    return "ok", ""


def _version_row(repo, rel, what):
    """`what` is the manifest's top-level section too (`extension` or `workflow`)."""
    section = what
    status, version = _manifest(repo, rel, section, "version")
    if status == "absent":
        return "warn", f"the {what} is not installed in this project (the CLI works without it)"
    if status != "ok" or not _MANIFEST_VERSION.fullmatch(version or ""):
        return "unknown", f"the installed {what} has no version doctor can read"
    if version != __version__:
        return "fail", f"the installed {what} is version {version} but the CLI is {__version__}: reinstall the {what} or the CLI so they match"
    return "ok", ""


def _config_text(msg, what):
    """The validator's own text, minus its file label: fixed wording that names keys by position and never a value."""
    tail = msg.split(": ", 1)[1] if ": " in msg else msg
    if tail.startswith("invalid JSON"):
        tail = "is not valid JSON"
    elif tail.startswith("cannot read"):
        tail = "cannot be read"
    return re.sub(r"[^\x20-\x7e]", "?", f"{what} {tail}")[:300]


def _load_config(repo):
    """(Config or None, (status, fix) for the `config` row)."""
    try:
        return Config.load(str(repo)), ("ok", "")
    except ConfigInvalid as e:
        return None, ("fail", _config_text(str(e), "ado.config.json:") + ": fix it, then run `ado doctor` again")
    except ConfigError:
        return None, ("fail", "no ado.config.json found: run `ado setup`")


def _load_target(repo):
    """(target Config or None, (status, fix) for `config-target`)."""
    base = strict.default_base(str(repo))
    if not base:
        return None, ("unknown", "no origin/HEAD, so the target branch is unknown (CI reads the config there): add the remote and run `git remote set-head origin -a`")
    try:
        target = Config.load_from_ref(base, repo=str(repo))
    except ConfigInvalid as e:
        return None, ("fail", _config_text(str(e), "") + ": CI cannot trust the target branch's config until this is fixed")
    if target is None:
        return None, ("warn", "the target branch has no ado.config.json yet, so CI judges a PR by its own: merge the setup")
    return target, ("ok", "")


def _probe(client):
    """('ok', identity dict) or (what went wrong: 'rejected' | 'network' | 'unknown', HTTP status or None). One GET."""
    try:
        data = client.get(CONNECTION)
    except AdoError as e:
        if e.kind == "signin" or (e.kind == "http" and e.status in (401, 403)):
            return "rejected", e.status
        if e.kind == "http" and e.status == 404:
            return "notfound", 404
        return ("network" if e.kind == "network" else "unknown"), e.status
    user = data.get("authenticatedUser") if isinstance(data, dict) else None
    return ("ok", user) if isinstance(user, dict) else ("unknown", None)


def _token_rows(cfg, client):
    """((token status, fix), (identity status, fix), identity id or None, the client used or None). The token is looked up, never printed."""
    source = cfg or getattr(client, "config", None)
    org = source.organization if source else None
    skip = ("unknown", "not checked: no usable ado.config.json, so the organization is unknown")
    if org is None:
        return skip, skip, None, None
    try:
        auth.load_token(org)
    except auth.AuthError:
        return ("fail", "no token: set $ADO_TOKEN or run `ado setup` to store a PAT"), ("unknown", "not checked: there is no token"), None, None
    except Exception as e:  # a secret store that fails is not a token
        why = f"the token store could not be read ({type(e).__name__})"
        return ("unknown", why), ("unknown", "not checked: " + why), None, None
    client = client or AdoClient(cfg, attempts=1, timeout=TIMEOUT)  # one attempt, short timeout: a diagnostic must not hang
    try:
        outcome, info = _probe(client)
    except auth.AuthError:
        return ("fail", "no token: set $ADO_TOKEN or run `ado setup` to store a PAT"), ("unknown", "not checked: there is no token"), None, client
    if outcome == "rejected":
        return (("fail", "ADO did not accept the token (a sign-in page, or HTTP 401/403): create a new PAT, check its expiry and organization, then run `ado setup`"),
                ("unknown", "not checked: ADO did not accept the token"), None, client)
    if outcome == "notfound":  # connectionData is organization-level: a 404 is a wrong organization
        return (("fail", "ADO answered HTTP 404: check `organization` in ado.config.json"), ("unknown", "not checked: the organization was not found"), None, client)
    if outcome in ("network", "unknown"):
        why = "could not reach Azure DevOps: check the network" if outcome == "network" else (
            f"Azure DevOps answered HTTP {info} (unexpected)" if isinstance(info, int) else "Azure DevOps gave no usable answer")
        return ("unknown", why), ("unknown", "not checked: " + why), None, client
    ident, descriptor = info.get("id"), info.get("descriptor")
    if is_anonymous(ident, descriptor):  # first: an anonymous descriptor with no id is still anonymous
        return (("fail", "ADO treated the request as anonymous, so the token was not accepted: create a new PAT and run `ado setup`"),
                ("fail", "connectionData answered as the anonymous user: the request carried no valid credentials, so nothing can be checked"), None, client)
    if not (isinstance(ident, str) and ident.strip()):  # nothing proves the token was accepted
        said = "ADO answered but did not say who you are"
        return ("unknown", said), ("unknown", said), None, client
    ident = comments._guid(ident)  # the verbs compare only plain identity GUIDs: braces, padding or any other form is refused there
    if ident is None:
        said = "ADO answered with an identity that is not an identity id"
        return ("ok", ""), ("unknown", said), None, client
    return ("ok", ""), ("ok", ""), ident, client


ONE_HUMAN = ("approvers must be separate human identities: identity binding protects against the agent only while an approver's credentials "
             "are not available to it (a separate account or browser profile, no approver PAT in the agent's environment)")


def _trust(repo):
    """(Trust, "") from `config.trusted` (the verbs' own path: target branch only, ADO_CONFIG and `unattended` included), or (None, a fixed
    refusal text). The validator's text is never echoed."""
    try:
        return config.trusted(repo), ""
    except ConfigInvalid as e:
        if str(e) in (config.E_NONROOT_TRUST, config.E_NO_TARGET):  # code-defined constants
            return None, str(e)
        return None, "the target branch's config cannot be read (see the config-target row), so the approval keys cannot be trusted"


def _approvers_row(repo, identity):
    """The `approvers` and `approval` keys come only from the target branch, read as the verbs read them (`config.trusted`): a refusal there
    is a `fail` with its fixed reason. Not checked here (they need a story's comments, which doctor does not read; known 0.3.0 limitation, see
    docs/configuration.md): the CI token identity and the authors of `[spec-kit-ado:*]` comments."""
    trust, why = _trust(repo)
    if trust is None:
        return "fail", why
    approvers = trust.approvers
    if trust.approval == "comment" and not approvers:
        return "fail", 'approval is "comment" on the target branch but no approvers are configured, so nobody can approve: add `approvers` there'
    if not approvers:
        return "ok", ""
    if identity is None:
        return "unknown", "approvers are configured, but the token identity is unknown, so doctor cannot compare them"
    if identity in approvers:
        return "fail", "an approver is the identity of this token, so the agent could approve itself: run the agent with a token that is not an approver"
    return "ok", ONE_HUMAN


BYPASS_FIX = ("this token identity holds the 'Bypass rules on work item updates' permission: run the agent as a Contributor without it "
              "(docs/configuration.md, 'Approvers and comment mode')")


def _bypass_row(repo, client, identity):
    """Reuses `approvals.bypass_bit` (two GETs: the project, then the permission). Only run once the token is accepted. Fail-closed: a bit that
    cannot be read is `unknown`, never `ok`."""
    if identity is None or client is None:
        return "unknown", "not checked: the token identity is unknown"
    bit = approvals.bypass_bit(client)
    if bit is False:
        return "ok", ""
    if bit is None:
        return "unknown", ("the 'Bypass rules on work item updates' permission could not be read (a restricted PAT may not read it); "
                           "comment mode refuses then: check it in the project's security settings")
    trust, _ = _trust(repo)
    if trust is None or trust.approval == "comment" or trust.approvers:  # an unreadable target may hold comment mode: judged as if it does
        return "fail", BYPASS_FIX + "; comment mode refuses while it is set"
    return "warn", BYPASS_FIX


def _bad(mode):
    return {"strict": "fail", "advisory": "warn"}.get(mode, "unknown")


def _stack_row(repo, cfg, mode):
    result, _, _ = stack.show(str(repo), mode or "advisory", cfg.strict["test_command"] if cfg else None)
    if result["status"] == "approved" and not result["notes"]:
        return "ok", ""
    if result["status"] == "none":
        return _bad(mode), f"no stack profile yet{' (optional)' if mode == 'advisory' else ''}: run `ado stack detect`"
    what = f"ado.stack.json is {result['status']}" if result["status"] != "approved" else f"the approved profile has {len(result['notes'])} note(s)"
    return _bad(mode), f"{what}: run `ado stack show` for the next step"


def _kits_row(repo, mode):
    try:
        findings = kits.verify(str(repo), kits.load_lock(str(repo)))["findings"]
    except (kits.KitError, ConfigInvalid):
        return _bad(mode), "the kit registry or ado.kits.lock.json cannot be trusted: run `ado kits verify`"
    if findings:
        return _bad(mode), f"{len(findings)} kit finding(s) (unpinned or changed): run `ado kits verify`"
    return "ok", ""


def _crash(e):
    """A fixed text plus the exception CLASS name (code-defined; never the message, which may carry user text)."""
    return "unknown", f"this check failed unexpectedly ({type(e).__name__}) and gave no result"


def _safe(fn, wrap, *args):
    """fn(*args), or `wrap(crash row)` when it raises: a diagnostic must report, not crash."""
    try:
        return fn(*args)
    except Exception as e:
        return wrap(_crash(e))


def run(repo=".", client=None, probe_item=None):
    """`{"rows": [{"id", "status", "fix"}], "scopes": {}}`, rows in `ROW_IDS` order. `client` is an AdoClient (default: one built from the
    config, only after a token was found); `probe_item` is accepted for the scope probes and ignored here."""
    out = {}
    row = lambda r: r  # noqa: E731
    out["git"] = _safe(_git_row, row, repo)
    out["specify"] = _safe(_specify_row, row, repo)
    out["shallow"] = _safe(_shallow_row, row, repo)
    cfg, out["config"] = _safe(_load_config, lambda r: (None, r), repo)
    target, out["config-target"] = _safe(_load_target, lambda r: (None, r), repo)
    out["token"], out["identity"], identity, client = _safe(_token_rows, lambda r: (r, r, None, None), cfg, client)
    out["extension"] = _safe(_version_row, row, repo, EXT_PATH, "extension")
    out["workflow"] = _safe(_version_row, row, repo, WF_PATH, "workflow")
    out["approvers"] = _safe(_approvers_row, row, repo, identity)
    out["bypass"] = _safe(_bypass_row, row, repo, client, identity)
    # the mode a stack or kit problem is judged by: strict if either config says so; unknown when the working-tree config is invalid
    invalid = cfg is None and out["config"][1].startswith("ado.config.json:")
    mode = "strict" if "strict" in (getattr(cfg, "mode", None), getattr(target, "mode", None)) else (None if invalid else "advisory")
    out["stack"] = _safe(_stack_row, row, repo, cfg, mode)
    out["kits"] = _safe(_kits_row, row, repo, mode)
    return {"rows": [{"id": i, "status": out[i][0], "fix": out[i][1]} for i in ROW_IDS], "scopes": {}}


def failed(result):
    return any(r["status"] == "fail" for r in result["rows"])


def render(result):
    """The text form: one line per row, `status  id  fix`."""
    return "\n".join(f"{r['status']:<8}{r['id']:<15}{r['fix']}".rstrip() for r in result["rows"])
