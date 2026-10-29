import textwrap
import re

from . import workitems
from .client import AdoError

TAG = "[sk:{id}]"
_TAG_RE = re.compile(r"^\[sk:([^\]]+)\]")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


MAX_TITLE = 255  # ADO rejects a longer System.Title (TF401324)


def _op(action, task_id, title, wid=None, state=None, truncated=False, child=None):
    # from_state/old_title: the existing child's current values (None for a create)
    return {"action": action, "task_id": task_id, "title": title,
            "work_item_id": wid, "state": state, "truncated": truncated,
            "from_state": child["state"] if child else None,
            "old_title": child["title"] if child else None}


def plan_push(client, parent_id, tasks):
    ids = [t["id"] for t in tasks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:  # would create duplicate [sk:Txxx] Tasks in one run
        raise ValueError(f"duplicate task id(s) in tasks.md: {', '.join(dupes)}")
    existing, extras = {}, []
    removed = client.config.removed_state
    # live children first (lowest id wins); a tag maps to a removed child only if all are removed
    for c in sorted(workitems.children(client, parent_id), key=lambda c: (c["state"] == removed, c["id"])):
        m = _TAG_RE.match(c["title"] or "")
        if m:  # untagged children are not ours: ignored entirely
            if m.group(1) in existing:  # the rest are reported only; extra removed ones are already gone
                if c["state"] != removed:
                    extras.append(_op("duplicate", m.group(1), c["title"], c["id"], child=c))
            else:
                existing[m.group(1)] = c
    done_state = client.config.done_state
    ops, seen = [], set()
    for t in tasks:
        seen.add(t["id"])
        title = f"{TAG.format(id=t['id'])} {t['title']}"
        cut = len(title) > MAX_TITLE  # keep the tag; cut the text so the result is stable per run
        if cut:
            title = title[:MAX_TITLE - 1] + "…"
        target = done_state if t["done"] else None  # never reopen/move unchecked tasks
        child = existing.get(t["id"])
        if child is None:
            ops.append(_op("create", t["id"], title, None, target, cut))
            continue
        if child["state"] == removed:  # never write to or target a Removed child
            ops.append(_op("removed", t["id"], title, child["id"], truncated=cut, child=child))
            continue
        need_state = target if target and child["state"] != target else None
        action = "update" if title != child["title"] or need_state else "unchanged"
        ops.append(_op(action, t["id"], title, child["id"], need_state, cut, child))
    ops += [_op("orphan", tid, c["title"], c["id"], child=c) for tid, c in existing.items() if tid not in seen]
    return ops + extras


def apply_push(client, parent_id, ops):
    cfg = client.config
    parent = [workitems.relation(client, "System.LinkTypes.Hierarchy-Reverse", parent_id)]
    res = {"created": [], "updated": [], "failed": []}
    for op in ops:
        if op["action"] not in ("create", "update"):
            continue
        wid = op["work_item_id"]  # None for a create until ADO returns its id
        try:
            if op["action"] == "create":
                wid = workitems.create(client, cfg.task_type, op["title"], relations=parent)["id"]
                if op["state"]:  # ADO may refuse a Done initial state; close after creating
                    workitems.update(client, wid, state=op["state"])
                res["created"].append({"task_id": op["task_id"], "work_item_id": wid,
                                       "title": op["title"]})
            else:
                workitems.update(client, op["work_item_id"], state=op["state"],
                                 fields={"System.Title": op["title"]})
                res["updated"].append({"task_id": op["task_id"],
                                       "work_item_id": op["work_item_id"], "title": op["title"]})
        except AdoError as e:
            res["failed"].append({"task_id": op["task_id"], "error": str(e), "work_item_id": wid})
    return res


def _cell(text):
    return " ".join(_CTRL.sub(" ", text).split()).replace("\\", "\\\\").replace("|", "\\|")


# Upstream's gate prints at most GateStep.MAX_SHOW_FILE_LINES = 200 lines of show_file
# (spec-kit src/specify_cli/workflows/step/gate/__init__.py), plus its own truncation line.
GATE_VISIBLE_LINES = 190


def gate_preview(text, path):
    """The plan text as written to --plan-out and compared by --expect-plan. A plan the gate cannot
    show in full gets a warning as its FIRST line (always visible); the file keeps the full text."""
    n = len(text.splitlines())
    if n <= GATE_VISIBLE_LINES:
        return text
    return (f"⚠ Preview longer than the approval gate can show ({n} lines) — open {path} "
            f"and read it in full before approving.\n{text}")


def _state_cell(o):
    if not o["state"]:
        return "-"
    return f"{_cell(o['from_state'])} → {_cell(o['state'])}" if o.get("from_state") else f"→ {_cell(o['state'])}"


def _title_cell(o):
    new = _cell(o["title"])
    if o["action"] == "update" and o.get("old_title") not in (None, o["title"]):
        new = f"{_cell(o['old_title'])} → {new}"
    return new + (" (cut to 255)" if o.get("truncated") else "")


def render_plan(parent_id, ops):
    """Compact: counts on line 2, then every row that writes or needs attention, then the unchanged
    task ids on one wrapped line, so a long plan still shows its writes first (the gate cuts at 200)."""
    counts = {a: sum(o["action"] == a for o in ops) for a in ("create", "update", "unchanged", "orphan", "duplicate", "removed")}
    lines = [f"Plan for work item {parent_id}", " · ".join(f"{a} {n}" for a, n in counts.items()), ""]
    if counts["orphan"] and not any(counts[a] for a in ("create", "update", "unchanged", "removed")):
        lines += [f"⚠ tasks.md lists none of the {counts['orphan']} Task(s) already on the board: was it emptied or "
                  "rewritten? Check it before approving.", ""]
    lines += ["| action | task | title | state | work item |", "|---|---|---|---|---|"]
    for o in ops:
        if o["action"] != "unchanged":
            lines.append(f"| {o['action']} | {_cell(o['task_id'])} | {_title_cell(o)} | {_state_cell(o)} | "
                         f"{o['work_item_id'] or '-'} |")
    same = [_cell(o["task_id"]) for o in ops if o["action"] == "unchanged"]
    if same:
        lines += ["", *textwrap.wrap(f"unchanged: {', '.join(same)} ({len(same)})", width=100,
                                     break_long_words=False, break_on_hyphens=False)]
    return "\n".join(lines) + "\n"
