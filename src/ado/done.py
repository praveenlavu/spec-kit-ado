import re
from pathlib import Path

from . import attachments, workitems
from .client import AdoError
from .dod import _visible, dod_file, section, unclosed
from .push import _cell

_BOX = re.compile(r"^[ \t>]*(?:[-*+]|\d+[.)])[ \t]+\[(.)\][ \t]*(.*)$", re.M)


UNCLOSED = "ledger has an unclosed code fence or comment"


def check(ledger_md):
    if unclosed(ledger_md):  # whatever follows it is hidden: never read it as ready
        return {"ready": False, "unticked": [UNCLOSED]}
    body = section(ledger_md, 5)
    if body is None:
        return {"ready": False, "unticked": ["section 5 missing"]}
    boxes = _BOX.findall(_visible(body)[0])  # a checklist example in a fence or comment is not a box
    if not boxes:
        return {"ready": False, "unticked": ["section 5 has no checklist"]}
    unticked = [t.strip() for mark, t in boxes if mark not in "xX"]
    return {"ready": not unticked, "unticked": unticked}


def _has(wi, name):
    """Is a file called `name` already attached to the fetched work item?"""
    return any(
        r.get("rel") == "AttachedFile"
        and (r.get("attributes", {}).get("name") == name or f"fileName={name}" in r.get("url", ""))
        for r in (wi or {}).get("relations") or [])


def _status(work_item, ledger_name, done_state):
    """(already in done_state, ledger already attached) from a fetched work item."""
    wi = work_item or {}
    return (wi.get("fields") or {}).get("System.State") == done_state, _has(wi, ledger_name)


def render_plan(work_item_id, chk, config, work_item=None, decisions=None):
    """`decisions`: path of Decisions_<id>.md when it exists (attached next to the ledger), else None."""
    head = f"Plan for closing work item {work_item_id}"
    if chk["ready"]:
        path = dod_file(config.dod_path, work_item_id)
        in_done, attached = _status(work_item, path.name, config.done_state)
        done_state = _cell(config.done_state)
        pending = [] if attached else [("ledger", path)]
        if decisions and not _has(work_item, Path(decisions).name):
            pending.append(("decisions file", decisions))
        if not pending:
            if in_done:
                return f"{head}\n\nDoD checklist complete. Already in {done_state}; nothing to do.\n"
            who = "Ledger and decisions file" if decisions else "Ledger"
            return (f"{head}\n\nDoD checklist complete. {who} already attached; "
                    f"will only set state to {done_state} on --apply.\n")
        steps = [f"attach {_cell(str(p))}" for _, p in pending] + ([] if in_done else [f"set state to {done_state}"])
        steps = "".join(f"{i}. {t}\n" for i, t in enumerate(steps, 1))
        if in_done:  # closed by hand: the files still go on, the state stays
            names = " and ".join(n for n, _ in pending)
            return (f"{head}\n\nDoD checklist complete. Already in {done_state}; {names} not attached yet. "
                    f"On --apply:\n{steps}")
        return f"{head}\n\nDoD checklist complete. On --apply:\n{steps}"
    return (f"{head}\n\nNot ready — no writes will happen, even with --apply. Unticked:\n"
            + "".join(f"- {_cell(u)}\n" for u in chk["unticked"]))


def apply(client, work_item_id, ledger_path, work_item=None, decisions=None, note=None):
    """`note`: a history line recorded with the state change (the sign-offs it relied on); nothing is recorded for a story already in done_state."""
    done_state = client.config.done_state
    in_done = _status(work_item, "", done_state)[0]
    files = [p for p in (ledger_path, decisions) if p and not _has(work_item, Path(p).name)]  # attached by name -> skipped
    res = attachments.attach(client, work_item_id, files, comment="DoD complete") if files else {"attached": [], "failed": []}
    if in_done:  # closed by hand or by an earlier run: attach what is missing, never touch the state
        skipped = {} if res["attached"] or res["failed"] else {"skipped": "already done"}
        return {**res, "state": done_state, "already_done": True, **skipped}
    if res["failed"]:  # never Done without its ledger attached
        return {**res, "state": None}
    try:
        workitems.update(client, work_item_id, state=done_state, fields={"System.History": note} if note else None)
    except AdoError as e:  # keep the attach result: it already happened
        return {**res, "state": None, "error": str(e)}
    return {**res, "state": done_state}
