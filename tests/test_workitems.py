import pytest

from ado import workitems
from tests.conftest import FakeClient


def test_get_expands_relations(cfg):
    client = FakeClient(cfg)
    workitems.get(client, 42)
    call = client.calls[0]
    assert call["method"] == "GET"
    assert "/Fabrikam/_apis/wit/workitems/42?$expand=relations&api-version=7.1" in call["path"]


def test_query_posts_wiql(cfg):
    client = FakeClient(cfg)
    workitems.query(client, "SELECT [System.Id] FROM WorkItems")
    call = client.calls[0]
    assert "/wit/wiql?api-version=7.1" in call["path"]
    assert call["body"] == {"query": "SELECT [System.Id] FROM WorkItems"}


def test_create_uses_dollar_type_and_patch_ops(cfg):
    client = FakeClient(cfg)
    workitems.create(client, "User Story", "Title X", fields={"System.AreaPath": "MC"})
    call = client.calls[0]
    assert "/wit/workitems/$User%20Story?api-version=7.1" in call["path"]
    assert call["content_type"] == "application/json-patch+json"
    assert {"op": "add", "path": "/fields/System.Title", "value": "Title X"} in call["body"]
    assert {"op": "add", "path": "/fields/System.AreaPath", "value": "MC"} in call["body"]


def test_update_state_and_comment(cfg):
    client = FakeClient(cfg)
    workitems.update(client, 7, state="Resolved")
    assert {"op": "add", "path": "/fields/System.State", "value": "Resolved"} in client.calls[0]["body"]
    workitems.comment(client, 7, "hello")
    assert {"op": "add", "path": "/fields/System.History", "value": "hello"} in client.calls[1]["body"]


def test_link_related_and_hyperlink_and_neither(cfg):
    client = FakeClient(cfg)
    workitems.link(client, 7, to_id=9)
    rel = client.calls[0]["body"][0]["value"]
    assert rel["rel"] == "System.LinkTypes.Related"
    assert rel["url"].endswith("/_apis/wit/workItems/9")
    workitems.link(client, 7, url="https://example.com/x")
    assert client.calls[1]["body"][0]["value"]["rel"] == "Hyperlink"
    with pytest.raises(ValueError):
        workitems.link(client, 7)


def test_link_parent(cfg):
    client = FakeClient(cfg)
    workitems.link(client, 7, parent=3)
    call = client.calls[0]
    assert call["body"] == [{"op": "add", "path": "/relations/-", "value": {
        "rel": "System.LinkTypes.Hierarchy-Reverse",
        "url": f"https://dev.azure.com/{cfg.organization}/_apis/wit/workItems/3"}}]
