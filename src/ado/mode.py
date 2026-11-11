"""`ado mode`: the role (bpo, tpo, dev, qa) the person at the keyboard has picked, kept in `.ado/mode.json` ({"role": "qa"}, no expiry).
It steers prompts only: gate, strict, done, approvals and CI never open this file (the agent can write it, so enforcing on it would be theatre).
With `roles.map` on the target branch, picking a role needs the token identity to hold it. The file is untrusted input: a fixed shape, at most
4 KiB, never a link; every error text here is fixed."""
import json
import os
import stat
from pathlib import Path

from .client import is_anonymous
from .config import ROLE_IDS
from .decisions import _atomic_write

MODE_DIR = Path(".ado")
MODE_FILE = MODE_DIR / "mode.json"
MAX_BYTES = 4096
E_FILE = "`.ado/mode.json` is not {\"role\": \"bpo|tpo|dev|qa\"} in a plain file: fix it or run `ado mode --clear`"
E_REQUIRE = "this step is for the role {roles}, but `.ado/mode.json` says otherwise: run `ado mode <role>` first"
E_ROLES = "--require takes roles from bpo, tpo, dev, qa, separated by commas, each once"
E_NOT_HELD = "the token identity does not hold the role {role} in `roles.map` on the target branch; nothing written"
E_IDENTITY = "cannot tell which identity the token belongs to; nothing written"


def read():
    """The picked role, or None when no file exists. A link, a non-file, an oversize file or any other shape is ValueError(E_FILE)."""
    try:
        if MODE_DIR.is_symlink():
            raise ValueError(E_FILE)
        st = os.lstat(MODE_FILE)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            raise ValueError(E_FILE)
        data = json.loads(MODE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise ValueError(E_FILE) from None
    if not (isinstance(data, dict) and list(data) == ["role"] and data["role"] in ROLE_IDS):
        raise ValueError(E_FILE)
    return data["role"]


def write(role):
    if MODE_DIR.is_symlink():
        raise ValueError(E_FILE)
    _atomic_write(MODE_FILE, json.dumps({"role": role}) + "\n")
    return role


def clear():
    try:
        MODE_FILE.unlink(missing_ok=True)
    except OSError:
        raise ValueError(E_FILE) from None


def require(csv):
    """Exit-0 check for a workflow step: no role picked, or the picked role is one of the listed ones. Never writes."""
    roles = csv.split(",")
    if not all(r in ROLE_IDS for r in roles) or len(set(roles)) != len(roles):
        raise ValueError(E_ROLES)
    current = read()
    if current is not None and current not in roles:
        raise ValueError(E_REQUIRE.format(roles=", ".join(roles)))
    return current


def check_identity(client, roles, role):
    """The token identity must hold `role` in the target branch's `roles.map`. A request error (AdoError) propagates: nothing is written."""
    user = client.get("/_apis/connectionData?api-version=7.1-preview")
    user = user.get("authenticatedUser") if isinstance(user, dict) else None
    me = user.get("id") if isinstance(user, dict) else None
    if not isinstance(me, str) or not me or is_anonymous(me, user.get("descriptor")):
        raise ValueError(E_IDENTITY)
    if role not in roles["map"].get(me.strip().lower(), ()):
        raise ValueError(E_NOT_HELD.format(role=role))
