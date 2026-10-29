import re

import pytest

from ado.client import AdoError
from ado.config import Config
from ado.push import apply_push, plan_push, render_plan
from tests.conftest import FakeClient

PARENT = 1001
LINK = {"rel": "System.LinkTypes.Hierarchy-Reverse",
        "url": f"https://dev.azure.com/contoso/_apis/wit/workItems/{PARENT}"}


def T(id, title, done=False):
    return {"id": id, "title": title, "done": done}


def kids(*rows):
    """Queued responses for children(): WIQL result then batch GET."""
    if not rows:
        return [{"workItems": []}]
    return [{"workItems": [{"id": r[0]} for r in rows]},
            {"value": [{"id": i, "fields": {"System.Title": t, "System.State": s}}
                       for i, t, s in rows]}]


def test_plan_create_update_unchanged_orphan(cfg):
    client = FakeClient(cfg, responses=kids(
        (1010, "[sk:T001] Same", "To Do"),
        (1011, "[sk:T002] Old name", "To Do"),
        (1012, "[sk:T009] Gone", "To Do"),
        (1013, "Hand-made task", "To Do"),
    ))
    ops = plan_push(client, PARENT, [T("T001", "Same"), T("T002", "New name"), T("T003", "Fresh")])
    got = {o["task_id"]: o for o in ops}
    assert got["T001"]["action"] == "unchanged" and got["T001"]["work_item_id"] == 1010
    assert got["T002"] == {"action": "update", "task_id": "T002", "title": "[sk:T002] New name",
                           "work_item_id": 1011, "state": None, "truncated": False,
                           "from_state": "To Do", "old_title": "[sk:T002] Old name"}
    assert got["T003"]["action"] == "create" and got["T003"]["work_item_id"] is None
    assert got["T003"]["title"] == "[sk:T003] Fresh"
    assert got["T009"]["action"] == "orphan" and got["T009"]["work_item_id"] == 1012
    assert len(ops) == 4  # untagged child ignored entirely


def test_children_none_skips_batch_get(cfg):
    client = FakeClient(cfg, responses=kids())
    plan_push(client, PARENT, [T("T001", "A")])
    assert [c["method"] for c in client.calls] == ["POST"]


def test_dry_run_makes_no_writes(cfg):
    client = FakeClient(cfg, responses=kids((1010, "[sk:T009] Gone", "To Do")))
    plan_push(client, PARENT, [T("T001", "A", done=True)])
    assert all(c["method"] == "GET" or (c["method"] == "POST" and "/wiql" in c["path"])
               for c in client.calls)


def test_apply_creates_with_parent_link_and_task_type():
    client = FakeClient(Config(organization="contoso", project="Fabrikam", task_type="Issue"),
                        responses=[{"id": 1020}])
    ops = [{"action": "create", "task_id": "T001", "title": "[sk:T001] A",
            "work_item_id": None, "state": None}]
    res = apply_push(client, PARENT, ops)
    (call,) = client.calls
    assert "/$Issue?" in call["path"] and call["method"] == "POST"
    assert {"op": "add", "path": "/relations/-", "value": LINK} in call["body"]
    assert {"op": "add", "path": "/fields/System.Title", "value": "[sk:T001] A"} in call["body"]
    assert res["created"] == [{"task_id": "T001", "work_item_id": 1020, "title": "[sk:T001] A"}]
    assert res["failed"] == []


class FlakyClient(FakeClient):
    """Raises AdoError on the 2nd create (POST to $Task)."""
    creates = 0

    def request(self, method, path, body=None, **kw):
        if method == "POST" and "/$Task" in path:
            self.creates += 1
            if self.creates == 2:
                raise AdoError("boom")
        return super().request(method, path, body, **kw)


def test_partial_failure_then_rerun_creates_only_missing(cfg):
    tasks = [T("T001", "A"), T("T002", "B"), T("T003", "C")]
    first = FlakyClient(cfg, responses=kids() + [{"id": 1020}, {"id": 1022}])
    ops = plan_push(first, PARENT, tasks)
    res = apply_push(first, PARENT, ops)
    assert [c["task_id"] for c in res["created"]] == ["T001", "T003"]
    assert res["failed"] == [{"task_id": "T002", "error": "boom", "work_item_id": None}]
    second = FakeClient(cfg, responses=kids((1020, "[sk:T001] A", "To Do"),
                                            (1022, "[sk:T003] C", "To Do")))
    ops2 = plan_push(second, PARENT, tasks)
    assert {o["task_id"]: o["action"] for o in ops2} == {
        "T001": "unchanged", "T002": "create", "T003": "unchanged"}


def test_renamed_task_updates_title(cfg):
    client = FakeClient(cfg, responses=kids((1010, "[sk:T001] Old", "To Do")) + [{}])
    ops = plan_push(client, PARENT, [T("T001", "New")])
    res = apply_push(client, PARENT, ops)
    call = client.calls[-1]
    assert call["method"] == "PATCH" and "/workitems/1010?" in call["path"]
    assert call["body"] == [{"op": "add", "path": "/fields/System.Title", "value": "[sk:T001] New"}]
    assert res["updated"][0]["work_item_id"] == 1010


def test_deleted_task_is_orphan_not_deleted(cfg):
    client = FakeClient(cfg, responses=kids((1012, "[sk:T009] Gone", "To Do")))
    ops = plan_push(client, PARENT, [])
    assert [o["action"] for o in ops] == ["orphan"]
    res = apply_push(client, PARENT, ops)
    assert res == {"created": [], "updated": [], "failed": []}
    assert all(c["method"] != "DELETE" for c in client.calls)
    assert len(client.calls) == 2  # only the plan's WIQL + batch GET


def test_done_task_uses_configured_done_state():
    cfg = Config(organization="contoso", project="Fabrikam", done_state="Closed")
    client = FakeClient(cfg, responses=kids((1010, "[sk:T001] A", "To Do"),
                                            (1011, "[sk:T002] B", "Closed")) + [{}])
    ops = {o["task_id"]: o for o in plan_push(
        client, PARENT, [T("T001", "A", done=True), T("T002", "B", done=True),
                         T("T003", "C", done=False)])}
    assert ops["T001"]["action"] == "update" and ops["T001"]["state"] == "Closed"
    assert ops["T002"]["action"] == "unchanged"
    assert ops["T003"]["state"] is None  # unchecked tasks are never moved
    apply_push(client, PARENT, [ops["T001"]])
    assert client.calls[-1]["body"][0] == {"op": "add", "path": "/fields/System.State", "value": "Closed"}


def test_apply_closes_done_task_after_create(cfg):
    client = FakeClient(cfg, responses=[{"id": 1020}, {}])
    ops = [{"action": "create", "task_id": "T001", "title": "[sk:T001] A",
            "work_item_id": None, "state": "Done"}]
    res = apply_push(client, PARENT, ops)
    assert client.calls[1]["method"] == "PATCH" and "/workitems/1020?" in client.calls[1]["path"]
    assert res["created"][0]["work_item_id"] == 1020


def test_render_plan_single_line_and_summary():
    ops = [{"action": "create", "task_id": "T001", "title": "[sk:T001] a|b\nc\x07d",
            "work_item_id": None, "state": None},
           {"action": "orphan", "task_id": "T9", "title": "[sk:T9] x", "work_item_id": 1012,
            "state": None}]
    out = render_plan(PARENT, ops)
    assert "| action | task | title | state | work item |" in out
    assert "a\\|b c d" in out
    assert "create 1 · update 0 · unchanged 0 · orphan 1 · duplicate 0 · removed 0" in out
    assert all(l.startswith("|") or not l.count("|") for l in out.splitlines())


def test_plan_shows_state_change_and_old_title():
    cfg = Config(organization="contoso", project="Fabrikam", done_state="Closed")
    client = FakeClient(cfg, responses=kids(
        (1010, "[sk:T001] Same", "New"),
        (1011, "[sk:T002] Old|name", "New"),
        (1012, "[sk:T004] Keep", "Closed"),
        (1013, "[sk:T005] Old", "New")))
    ops = plan_push(client, PARENT, [T("T001", "Same", done=True), T("T002", "New name"),
                                     T("T003", "Fresh", done=True), T("T004", "Keep", done=True),
                                     T("T005", "Renamed", done=True)])
    got = {o["task_id"]: o for o in ops}
    assert (got["T001"]["from_state"], got["T001"]["state"]) == ("New", "Closed")
    assert (got["T003"]["from_state"], got["T003"]["old_title"]) == (None, None)
    out = render_plan(PARENT, ops)
    assert "| update | T001 | [sk:T001] Same | New → Closed | 1010 |" in out
    assert "| update | T005 | [sk:T005] Old → [sk:T005] Renamed | New → Closed | 1013 |" in out
    assert "| update | T002 | [sk:T002] Old\\|name → [sk:T002] New name | - | 1011 |" in out
    assert "| create | T003 | [sk:T003] Fresh | → Closed | - |" in out
    assert "| unchanged |" not in out and "unchanged: T004 (1)" in out
    assert out == render_plan(PARENT, ops)


def test_cell_escapes_backslash_so_rows_keep_column_count():
    ops = [{"action": "update", "task_id": "T1", "title": "[sk:T1] new\\|x|y", "work_item_id": 7,
            "state": "Closed", "truncated": False, "from_state": "New",
            "old_title": "[sk:T1] a\\|b|c\\"}]
    row = render_plan(PARENT, ops).splitlines()[5]
    assert "a\\\\\\|b\\|c\\\\ → [sk:T1] new\\\\\\|x\\|y | New → Closed | 7 |" in row
    # unescaped pipes (not preceded by an odd run of backslashes) must be exactly the 6 delimiters
    assert len(re.findall(r"(?<!\\)(?:\\\\)*\|", row)) == 6


def test_plan_rejects_duplicate_task_ids_before_any_call(cfg):
    client = FakeClient(cfg)
    with pytest.raises(ValueError, match="T001"):
        plan_push(client, PARENT, [T("T001", "A"), T("T002", "B"), T("T001", "C")])
    assert client.calls == []


def test_children_wiql_orders_by_id(cfg):
    client = FakeClient(cfg, responses=kids())
    plan_push(client, PARENT, [])
    assert client.calls[0]["body"]["query"].endswith("ORDER BY [System.Id]")



def test_duplicate_tagged_children_reported_never_applied(cfg):
    client = FakeClient(cfg, responses=kids((1010, "[sk:T001] A", "To Do"),
                                            (1011, "[sk:T001] A copy", "To Do"),
                                            (1012, "[sk:T001] A copy 2", "To Do")))
    ops = plan_push(client, PARENT, [T("T001", "A", done=True)])
    assert [(o["action"], o["work_item_id"]) for o in ops] == [
        ("update", 1010), ("duplicate", 1011), ("duplicate", 1012)]
    assert ops[1]["title"] == "[sk:T001] A copy" and ops[1]["task_id"] == "T001"
    assert "duplicate 2" in render_plan(PARENT, ops)
    apply_push(client, PARENT, [o for o in ops if o["action"] == "duplicate"])
    assert len(client.calls) == 2  # only the plan's WIQL + batch GET


def test_removed_child_reported_never_written_or_orphaned(cfg):
    client = FakeClient(cfg, responses=kids((1010, "[sk:T001] Old", "Removed")))
    ops = plan_push(client, PARENT, [T("T001", "New", done=True)])
    assert ops == [{"action": "removed", "task_id": "T001", "title": "[sk:T001] New",
                    "work_item_id": 1010, "state": None, "truncated": False,
                    "from_state": "Removed", "old_title": "[sk:T001] Old"}]
    assert "removed 1" in render_plan(PARENT, ops) and "orphan 0" in render_plan(PARENT, ops)
    res = apply_push(client, PARENT, ops)
    assert res == {"created": [], "updated": [], "failed": []}
    assert len(client.calls) == 2


def test_live_recreated_child_beats_lower_id_removed_one(cfg):
    client = FakeClient(cfg, responses=kids((1010, "[sk:T001] A", "Removed"),
                                            (1011, "[sk:T001] A", "Removed"),
                                            (1012, "[sk:T001] A", "To Do")) + [{}])
    ops = plan_push(client, PARENT, [T("T001", "A", done=True)])
    assert [(o["action"], o["work_item_id"]) for o in ops] == [("update", 1012)]


class StateRefusedClient(FakeClient):
    """Create succeeds; the follow-up state PATCH is refused."""

    def request(self, method, path, body=None, **kw):
        if method == "PATCH":
            raise AdoError("state refused")
        return super().request(method, path, body, **kw)


def test_failed_state_after_create_keeps_work_item_id(cfg):
    tasks = [T("T003", "Fresh", done=True)]
    first = StateRefusedClient(cfg, responses=kids() + [{"id": 99}])
    ops = plan_push(first, PARENT, tasks)
    res = apply_push(first, PARENT, ops)
    assert res["created"] == []
    assert res["failed"] == [{"task_id": "T003", "error": "state refused", "work_item_id": 99}]
    second = FakeClient(cfg, responses=kids((99, "[sk:T003] Fresh", "To Do")))
    ops2 = plan_push(second, PARENT, tasks)
    assert [(o["task_id"], o["action"], o["work_item_id"]) for o in ops2] == [
        ("T003", "update", 99)]  # maps to the created Task; no second create


def test_long_title_cut_to_255_and_stable_on_rerun(cfg):
    long_text = "x" * 400
    ops = plan_push(FakeClient(cfg, responses=kids()), PARENT, [T("T001", long_text)])
    title = ops[0]["title"]
    assert len(title) == 255 and title.startswith("[sk:T001] x") and title.endswith("x…")
    assert ops[0]["truncated"] is True
    assert "(cut to 255)" in render_plan(PARENT, ops)
    again = plan_push(FakeClient(cfg, responses=kids((1010, title, "To Do"))), PARENT,
                      [T("T001", long_text)])
    assert again[0]["action"] == "unchanged" and again[0]["truncated"] is True


def test_title_of_exactly_255_is_not_cut(cfg):
    text = "y" * (255 - len("[sk:T001] "))
    ops = plan_push(FakeClient(cfg, responses=kids()), PARENT, [T("T001", text)])
    assert len(ops[0]["title"]) == 255 and not ops[0]["title"].endswith("…")
    assert ops[0]["truncated"] is False
    assert "(cut to 255)" not in render_plan(PARENT, ops)


def _op(action, tid):
    return {"action": action, "task_id": tid, "title": f"[sk:{tid}] t", "work_item_id": None if action == "create" else 7,
            "state": None, "truncated": False}


def test_render_plan_counts_second_and_unchanged_collapsed():
    ops = [_op("unchanged", "T001"), _op("create", "T002"), _op("unchanged", "T003")]
    lines = render_plan(PARENT, ops).splitlines()
    assert lines[1] == "create 1 · update 0 · unchanged 2 · orphan 0 · duplicate 0 · removed 0"
    assert "unchanged: T001, T003 (2)" in lines and "| create | T002 | [sk:T002] t | - | - |" in lines
    assert not any(l.startswith("| unchanged") for l in lines)


def test_render_plan_300_tasks_two_writes_fit_the_gate():
    ops = [_op("unchanged", f"T{i:03}") for i in range(300)]
    ops[150], ops[290] = _op("update", "T150"), _op("create", "T290")
    out = render_plan(PARENT, ops)
    head = out.splitlines()[:20]
    assert any("| update | T150 |" in l for l in head) and any("| create | T290 |" in l for l in head)
    assert len(out.splitlines()) < 40 and "(298)" in out
    assert out == render_plan(PARENT, ops)


def test_gate_preview_190_191_boundary():
    from ado.push import gate_preview
    assert gate_preview("x\n" * 190, "p.md") == "x\n" * 190
    out = gate_preview("x\n" * 191, "p.md")
    assert out.splitlines()[0].startswith("⚠ ") and "(191 lines)" in out.splitlines()[0] and "p.md" in out.splitlines()[0]
    assert out.endswith("x\n" * 191)


def test_render_plan_warns_when_every_tracked_task_is_an_orphan(cfg):
    # R3 N3: an emptied tasks.md shows up as all-orphan; the gate must not read as routine
    client = FakeClient(cfg, responses=kids((1, "[sk:T001] A", "New"), (2, "[sk:T002] B", "New")))
    out = render_plan(PARENT, plan_push(client, PARENT, []))
    assert "⚠ tasks.md lists none of the 2 Task(s) already on the board" in out
    assert out.splitlines()[0] == f"Plan for work item {PARENT}"
    client = FakeClient(cfg, responses=kids((1, "[sk:T001] A", "New"), (2, "[sk:T009] Gone", "New")))
    assert "⚠" not in render_plan(PARENT, plan_push(client, PARENT, [T("T001", "A")]))
