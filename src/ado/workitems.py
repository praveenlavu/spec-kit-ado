import urllib.parse


def _proj(client):
    return urllib.parse.quote(client.config.project)


def _api(client):
    return client.config.api_version


def get(client, work_item_id):
    return client.get(
        f"/{_proj(client)}/_apis/wit/workitems/{work_item_id}"
        f"?$expand=relations&api-version={_api(client)}"
    )


def query(client, wiql):
    return client.post(
        f"/{_proj(client)}/_apis/wit/wiql?api-version={_api(client)}", {"query": wiql}
    )


def children(client, parent_id):
    ids = [w["id"] for w in query(
        client, f"SELECT [System.Id] FROM WorkItems WHERE [System.Parent] = {int(parent_id)} "
        "ORDER BY [System.Id]"
    ).get("workItems", [])]
    out = []
    for i in range(0, len(ids), 200):  # ADO caps a batch GET at 200 ids
        chunk = ",".join(map(str, ids[i:i + 200]))
        res = client.get(
            f"/{_proj(client)}/_apis/wit/workitems?ids={chunk}"
            f"&fields=System.Title,System.State&api-version={_api(client)}"
        )
        out += [{"id": v["id"], "title": v["fields"].get("System.Title", ""),
                 "state": v["fields"].get("System.State", "")} for v in res.get("value", [])]
    return out


def create(client, wi_type, title, fields=None, relations=None):
    ops = [{"op": "add", "path": "/fields/System.Title", "value": title}]
    for name, value in (fields or {}).items():
        ops.append({"op": "add", "path": f"/fields/{name}", "value": value})
    for rel in relations or []:
        ops.append({"op": "add", "path": "/relations/-", "value": rel})
    type_seg = urllib.parse.quote(f"${wi_type}", safe="$")
    return client.post(
        f"/{_proj(client)}/_apis/wit/workitems/{type_seg}?api-version={_api(client)}",
        ops,
        content_type="application/json-patch+json",
    )


def update(client, work_item_id, state=None, fields=None):
    ops = []
    if state:
        ops.append({"op": "add", "path": "/fields/System.State", "value": state})
    for name, value in (fields or {}).items():
        ops.append({"op": "add", "path": f"/fields/{name}", "value": value})
    return client.patch(
        f"/{_proj(client)}/_apis/wit/workitems/{work_item_id}?api-version={_api(client)}",
        ops,
        content_type="application/json-patch+json",
    )


def comment(client, work_item_id, text):
    return update(client, work_item_id, fields={"System.History": text})


def relation(client, rel, work_item_id):
    return {"rel": rel, "url": f"https://dev.azure.com/{client.config.organization}"
                               f"/_apis/wit/workItems/{work_item_id}"}


def link(client, work_item_id, to_id=None, url=None, parent=None):
    if to_id:
        value = relation(client, "System.LinkTypes.Related", to_id)
    elif parent:
        value = relation(client, "System.LinkTypes.Hierarchy-Reverse", parent)
    elif url:
        value = {"rel": "Hyperlink", "url": url}
    else:
        raise ValueError("link: provide to_id (related), parent (parent work item) or url (hyperlink)")
    return add_relation(client, work_item_id, value)


def add_relation(client, work_item_id, value):
    return client.patch(
        f"/{_proj(client)}/_apis/wit/workitems/{work_item_id}?api-version={_api(client)}",
        [{"op": "add", "path": "/relations/-", "value": value}],
        content_type="application/json-patch+json",
    )
