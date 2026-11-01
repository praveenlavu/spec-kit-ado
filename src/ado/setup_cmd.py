import copy
import getpass as _getpass
import json
import os
import sys
import urllib.parse
from pathlib import Path

from .auth import TOKEN_ENV, store_token
from .client import AdoClient
from .config import ORG_RE, Config, ConfigError, reject_control_chars, valid_org

# `ado setup --profile` (D§4.1): exactly these keys, nothing else. None is a value setup works out (strict.test_command).
# team = starter's `mode` plus the two lists; strict leaves the lists to its floor (STRICT_GATE_BLOCK / STRICT_REVIEW_BLOCK).
PROFILES = {
    "starter": {"mode": "advisory"},
    "team": {"mode": "advisory", "gate": {"block": ["ac_traceability", "open_questions"]}, "review_block_on": ["blocker"]},
    "strict": {"mode": "strict", "strict": {"test_command": None}},
}
_MISSING = object()
_OBJECT = object()
_SHOW_MAX = 80  # characters of one value in the diff


def _states(client, proj_seg, wit, api):
    """Return {category: [state names]} for one work item type."""
    out = {}
    for st in client.get(f"/{proj_seg}/_apis/wit/workitemtypes/"
                         f"{urllib.parse.quote(wit)}/states?api-version={api}")["value"]:
        out.setdefault(st["category"], []).append(st["name"])
    return out


def _detect(client, config, proj_seg, data):
    """Look up story type / done / removed states for keys not already in `data`.

    Returns (found, done_choices). done_choices is non-empty when the story and task
    types disagree on the Completed state (caller asks). Any failure returns what was
    found so far: a lookup problem must never abort a successful setup.
    """
    found, choices = {}, []
    try:
        story = data.get("story_type") or client.get(
            f"/{proj_seg}/_apis/wit/workitemtypecategories/Microsoft.RequirementCategory"
            f"?api-version={config.api_version}")["defaultWorkItemType"]["name"]
        found["story_type"] = story
        if "done_state" in data and "removed_state" in data:
            return found, choices
        story_states = _states(client, proj_seg, story, config.api_version)
        if "removed_state" not in data and story_states.get("Removed"):
            found["removed_state"] = story_states["Removed"][0]
        if "done_state" not in data:
            task_states = _states(client, proj_seg, data.get("task_type") or "Task",
                                  config.api_version)
            story_done = story_states.get("Completed", [])
            task_done = task_states.get("Completed", [])
            common = [n for n in story_done if n in task_done]
            if len(common) == 1:
                found["done_state"] = common[0]
            else:
                choices = sorted(set(story_done + task_done))
    except Exception as e:  # noqa: BLE001 - detection is best-effort
        print(f"Note: could not auto-detect work item types/states ({e}); "
              "keeping defaults for any not found.")
    return found, choices


def _merge_profile(data, profile, test_command):
    """A copy of `data` with the profile's keys set. Nothing is removed; a list the profile sets is added to, never cut."""
    out = copy.deepcopy(data)
    for key, value in profile.items():
        if not isinstance(value, dict):
            out[key] = _union(out.get(key), value)
            continue
        sets = {k: test_command if v is None else v for k, v in value.items()}  # None: strict.test_command, typed or left alone
        sets = {k: v for k, v in sets.items() if v is not None}
        node = out.setdefault(key, {}) if sets else {}
        if not isinstance(node, dict):
            raise ConfigError(f"'{key}' must be a JSON object; not overwriting")
        for k, v in sets.items():
            node[k] = _union(node.get(k), v)
    return out


def _union(old, new):
    return old + [x for x in new if x not in old] if isinstance(new, list) and isinstance(old, list) else new


def _flat(node, prefix=""):
    """{dotted key: leaf} where a leaf is anything but a non-empty object."""
    if isinstance(node, dict) and node:
        out = {}
        for k, v in node.items():
            out.update(_flat(v, f"{prefix}.{k}" if prefix else k))
        return out
    return {prefix: node}


def _shown(value):
    """The value as JSON when it is short and no secret pattern, else None (the diff then says `changed`)."""
    from .stack import SECRETS
    text = json.dumps(value, ensure_ascii=True)  # control characters and non-ASCII come out escaped
    return text if len(text) <= _SHOW_MAX and not any(pat.search(text) for _, pat in SECRETS) else None


def diff_lines(old, new):
    """`key: old -> new` for every leaf key whose value differs; a value that is long or looks like a secret is not shown (`key: changed`)."""
    a, b = _flat(old), _flat(new)
    out = []
    for key, value in b.items():
        before = a.get(key, _MISSING)
        if before == value:
            continue
        if before is _MISSING and _is_object(old, key):  # an object (or an emptied one) stood where a value is written
            before = _OBJECT
        s_new = _shown(value)
        s_old = "(not set)" if before is _MISSING else "(an object, replaced)" if before is _OBJECT else _shown(before)
        out.append(f"{key}: {s_old} -> {s_new}" if s_old and s_new else f"{key}: changed")
    return out


def _strict_test_command(profile, data, path, input_fn):
    """The `strict.test_command` to write for the strict profile, or None: kept when already set, left to `ado stack approve` when the
    stack profile is approved, else asked (blank leaves it unset)."""
    if profile != "strict" or (isinstance(data.get("strict"), dict) and data["strict"].get("test_command")):
        return None
    from . import stack
    if stack.read_profile(str(path.resolve().parent))["status"] == "approved":
        print("strict.test_command comes from the approved stack profile: `ado stack approve --apply` writes it.")
        return None
    return input_fn("Command that runs your tests in strict mode (blank to set it later): ").strip() or None


def _write_config(path, raw, data):
    """Atomic write (temp file in the same directory + os.replace, read back); an existing file keeps its indent and line endings."""
    from . import stack

    def check(blob):
        try:
            Config._from_data(json.loads(blob.decode("utf-8")), path.name)
        except ConfigError:
            raise ValueError(f"{path.name}: what was about to be written is not a valid config; nothing changed") from None
    text = stack._config_text(raw.decode("utf-8"), data) if raw is not None else json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    stack._atomic_replace(path, text, check=check, expect=raw)


def _is_object(node, dotted):
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, dict)


def _stdio_is_terminal():
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())


def run_setup(input_fn=input, getpass_fn=None, out_path="ado.config.json", profile=None, interactive=None):
    getpass_fn = getpass_fn or _getpass.getpass
    path = Path(out_path)
    data, raw = {}, None
    if path.exists():  # validate first: never destroy a file we cannot read
        try:
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as e:  # UnicodeDecodeError is a ValueError
            raise ConfigError(f"{path}: cannot read as JSON ({e}); not overwriting")
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: must be a JSON object; not overwriting")

    def ask(label, key):
        cur = data.get(key) if isinstance(data.get(key), str) else ""
        # the file may come from a clone: show the default escaped and capped, never raw (ANSI/OSC would reach the terminal)
        shown = f" [{_shown(cur) or 'existing value, too long to show'}]" if cur else ""
        return input_fn(f"{label}{shown}: ").strip() or cur

    org = ask("Azure DevOps organization (https://dev.azure.com/<org>)", "organization")
    if not valid_org(org):  # before any prompt or request: org feeds a URL and the token path
        raise ConfigError(f"organization {org!r} is invalid: must match {ORG_RE.pattern}")
    project = ask("Project name", "project")

    print(f"Create a PAT: https://dev.azure.com/{org}/_usersSettings/tokens "
          "(scopes: Work Items - Read & Write; Project and Team - Read; Code - Read for `ado branch --link`; "
          "Code - Read & Write for `ado pr create` on Azure Repos).")
    if profile is not None and path.is_symlink():
        raise ConfigError(f"{path}: is a symlink; not overwriting")
    pat = getpass_fn("Paste your PAT (input hidden): ").strip()
    if not pat:
        print("No PAT entered — aborting setup (nothing stored).")
        return 2

    config = Config(organization=org, project=project)
    client = AdoClient(config)
    proj_seg = urllib.parse.quote(project)
    # Verify with the typed PAT (load_token prefers $ADO_TOKEN); store only once it works.
    saved = os.environ.get(TOKEN_ENV)
    os.environ[TOKEN_ENV] = pat
    try:
        info = client.get(f"/_apis/projects/{proj_seg}?api-version={config.api_version}")
        found, done_choices = _detect(client, config, proj_seg, data)
    finally:
        if saved is None:
            os.environ.pop(TOKEN_ENV, None)
        else:
            os.environ[TOKEN_ENV] = saved
    if done_choices:  # story and task types disagree on the closed state: ask
        found["done_state"] = (input_fn(f"Done state ({' / '.join(done_choices)}) "
                                        f"[{done_choices[0]}]: ").strip() or done_choices[0])
    before = copy.deepcopy(data)
    data.update(organization=org, project=project, **found)
    lines = []
    if profile is not None:
        data = _merge_profile(data, PROFILES[profile], _strict_test_command(profile, data, path, input_fn))
        Config._from_data(data, path.name)  # ConfigInvalid names the key, never the value
        lines = diff_lines(before, data) if raw is not None else []
        if lines:  # an existing config: show what changes, write only on an explicit y from a terminal
            print(f"Changes to {path.name}:")
            print("\n".join(lines))
            if before.get("mode") == "strict" and data.get("mode") != "strict":
                print("Warning: this turns strict mode off for the project.")
            if not (_stdio_is_terminal() if interactive is None else interactive):
                print("Not a terminal: nothing written.")
                return 2
            if input_fn("Write these changes? [y/N]: ").strip().lower() != "y":
                print("Nothing written.")
                return 2
    reject_control_chars(data, "", out_path)  # before storing the token or writing: never write what load() refuses
    where = store_token(org, pat)

    if profile is None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif raw is None or lines:
        _write_config(path, raw, data)
    wrote = f"wrote {out_path}" if profile is None or raw is None or lines else f"nothing to change in {out_path}"
    print(f"Verified project '{info.get('name', project)}' — "
          f"stored credential in {where}; {wrote}.")
    gi = path.resolve().parent / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if ".ado/" not in (ln.strip() for ln in text.splitlines()):
        gi.write_text(text + ("\n" if text and not text.endswith("\n") else "") + ".ado/\n",
                      encoding="utf-8")
    return 0
