import getpass as _getpass
import json
import os
import urllib.parse
from pathlib import Path

from .auth import TOKEN_ENV, store_token
from .client import AdoClient
from .config import ORG_RE, Config, ConfigError, reject_control_chars, valid_org


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


def run_setup(input_fn=input, getpass_fn=None, out_path="ado.config.json"):
    getpass_fn = getpass_fn or _getpass.getpass
    path = Path(out_path)
    data = {}
    if path.exists():  # validate first: never destroy a file we cannot read
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:  # UnicodeDecodeError is a ValueError
            raise ConfigError(f"{path}: cannot read as JSON ({e}); not overwriting")
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: must be a JSON object; not overwriting")

    def ask(label, key):
        cur = data.get(key) if isinstance(data.get(key), str) else ""
        shown = f" [{cur}]" if cur else ""
        return input_fn(f"{label}{shown}: ").strip() or cur

    org = ask("Azure DevOps organization (https://dev.azure.com/<org>)", "organization")
    if not valid_org(org):  # before any prompt or request: org feeds a URL and the token path
        raise ConfigError(f"organization {org!r} is invalid: must match {ORG_RE.pattern}")
    project = ask("Project name", "project")

    print(f"Create a PAT: https://dev.azure.com/{org}/_usersSettings/tokens "
          "(scopes: Work Items - Read & Write; Project and Team - Read; Code - Read for `ado branch --link`; "
          "Code - Read & Write for `ado pr create` on Azure Repos).")
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
    data.update(organization=org, project=project, **found)
    reject_control_chars(data, "", out_path)  # before storing the token or writing: never write what load() refuses
    where = store_token(org, pat)

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print(f"Verified project '{info.get('name', project)}' — "
          f"stored credential in {where}; wrote {out_path}.")
    gi = path.resolve().parent / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if ".ado/" not in (ln.strip() for ln in text.splitlines()):
        gi.write_text(text + ("\n" if text and not text.endswith("\n") else "") + ".ado/\n",
                      encoding="utf-8")
    return 0
