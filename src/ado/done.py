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


def _status(work_item, ledger_name, done_state):
    """(already in done_state, ledger already attached) from a fetched work item."""
    wi = work_item or {}
    in_done = (wi.get("fields") or {}).get("System.State") == done_state
    attached = any(
        r.get("rel") == "AttachedFile"
        and (r.get("attributes", {}).get("name") == ledger_name or f"fileName={ledger_name}" in r.get("url", ""))
        for r in wi.get("relations") or [])
    return in_done, attached


def render_plan(work_item_id, chk, config, work_item=None):
    head = f"Plan for closing work item {work_item_id}"
    if chk["ready"]:
        path = dod_file(config.dod_path, work_item_id)
        in_done, attached = _status(work_item, path.name, config.done_state)
        done_state = _cell(config.done_state)
        if in_done and attached:
            return f"{head}\n\nDoD checklist complete. Already in {done_state}; nothing to do.\n"
        if in_done:  # closed by hand: the ledger still goes on, the state stays
            return (f"{head}\n\nDoD checklist complete. Already in {done_state}; ledger not attached yet. "
                    f"On --apply:\n1. attach {_cell(str(path))}\n")
        if attached:
            return (f"{head}\n\nDoD checklist complete. Ledger already attached; "
                    f"will only set state to {done_state} on --apply.\n")
        return (f"{head}\n\nDoD checklist complete. On --apply:\n"
                f"1. attach {_cell(str(path))}\n2. set state to {done_state}\n")
    return (f"{head}\n\nNot ready — no writes will happen, even with --apply. Unticked:\n"
            + "".join(f"- {_cell(u)}\n" for u in chk["unticked"]))


def apply(client, work_item_id, ledger_path, work_item=None):
    done_state = client.config.done_state
    in_done, attached = _status(work_item, Path(ledger_path).name, done_state)
    res = {"attached": [], "failed": []} if attached else attachments.attach(
        client, work_item_id, [ledger_path], comment="DoD complete")
    if in_done:  # closed by hand or by an earlier run: attach the ledger if it is missing, never touch the state
        skipped = {} if res["attached"] or res["failed"] else {"skipped": "already done"}
        return {**res, "state": done_state, "already_done": True, **skipped}
    if res["failed"]:  # never Done without its ledger attached
        return {**res, "state": None}
    try:
        workitems.update(client, work_item_id, state=done_state)
    except AdoError as e:  # keep the attach result: it already happened
        return {**res, "state": None, "error": str(e)}
    return {**res, "state": done_state}
