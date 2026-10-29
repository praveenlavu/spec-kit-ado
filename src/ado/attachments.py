import os
import tempfile
import unicodedata
import urllib.parse
from pathlib import Path

from . import comments, workitems
from .client import AdoError, trusted_url


class BlockedFile(Exception):
    """Path refused by the sensitive-data attachment guard."""


def assert_safe(path, allow):
    p = Path(path)
    if not p.is_file():
        raise BlockedFile(f"{p}: not a file")
    ext = p.suffix.lower()
    allow = [a.lower() for a in allow]
    if ext not in allow:
        raise BlockedFile(
            f"{p}: extension '{ext or '(none)'}' is not on the sensitive-data-safe allowlist "
            f"({', '.join(sorted(allow))}). Refusing to attach."
        )
    return p


def upload(client, path):
    p = assert_safe(path, client.config.attachments_allow)
    api = client.config.api_version
    proj = urllib.parse.quote(client.config.project)
    name = urllib.parse.quote(p.name)
    result = client.post(
        f"/{proj}/_apis/wit/attachments?fileName={name}&api-version={api}",
        p.read_bytes(),
        content_type="application/octet-stream",
        raw=True,
    )
    return result["url"]


def attach(client, work_item_id, paths, comment=""):
    api = client.config.api_version
    proj = urllib.parse.quote(client.config.project)
    results = {"attached": [], "failed": []}
    for path in paths:
        try:
            url = upload(client, path)
            client.patch(
                f"/{proj}/_apis/wit/workitems/{work_item_id}?api-version={api}",
                [
                    {
                        "op": "add",
                        "path": "/relations/-",
                        "value": {
                            "rel": "AttachedFile",
                            "url": url,
                            "attributes": {"comment": comment},
                        },
                    }
                ],
                content_type="application/json-patch+json",
            )
            results["attached"].append({"path": str(path), "url": url})
        except Exception as e:  # per-file isolation (spec EH3)
            results["failed"].append({"path": str(path), "error": str(e)})
    return results


# ---- reading a story's attachments (N2): ADO-controlled names and bytes, untrusted ----
MAX_BYTES = 5 * 1024 * 1024  # counted on bytes actually read, never on the declared resourceSize


def safe_dir(d):
    """The download dir resolved (symlinks followed) and strictly inside .ado/attachments/, away from the
    PR body, PR args and plan files kept elsewhere in .ado/; ValueError otherwise."""
    path = comments.under_ado(d, "attachments")
    if path is None or (path.name == "attachments" and path.parent.name == ".ado"):
        raise ValueError(f"--dir must be inside .ado/attachments/ (got {d})")
    return path


def _skip_reason(rel, config):
    name = rel["attributes"].get("name")
    if not trusted_url(rel.get("url"), config.organization):
        return "untrusted host"
    if (not isinstance(name, str) or not name or name.startswith(".") or ".." in name
            or any(c in "/\\:" or unicodedata.category(c)[0] == "C" for c in name)):  # controls, bidi overrides
        return "unsafe name"
    if Path(name).suffix.lower() not in [a.lower() for a in config.attachments_allow]:
        return "not on attachments_allow"
    return None


def _unique(name, used):
    """Same-name attachments keep every copy: oldest `a.md`, then `a (2).md`, `a (3).md`. Compared NFC +
    casefold, as APFS and case-insensitive filesystems would treat them as one file."""
    stem, ext, n, out = Path(name).stem, Path(name).suffix, 1, name
    key = lambda s: unicodedata.normalize("NFC", s).casefold()  # noqa: E731
    while key(out) in used:
        n += 1
        out = f"{stem} ({n}){ext}"
    used.add(key(out))
    return out


def _write(out_dir, name, data):
    """Write via a temp file + os.replace in out_dir: a symlink already at `name` is replaced, never
    followed, and a failed write leaves no partial file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".part-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, out_dir / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return out_dir / name


def fetch(client, work_item_id, out_dir=None):
    """The story's AttachedFile relations as [{name, size, id}], oldest first. With out_dir (from
    safe_dir), download each allowed one there: `path` on success, else `skipped` or `error`."""
    rels = [r for r in workitems.get(client, work_item_id).get("relations") or []
            if r.get("rel") == "AttachedFile" and isinstance(r.get("attributes"), dict)]
    rels.sort(key=lambda r: (str(r["attributes"].get("resourceCreatedDate", "")),
                             str(r["attributes"].get("id")).rjust(20)))
    out, used = [], set()
    for r in rels:
        a = r["attributes"]
        item = {"name": a.get("name"), "size": a.get("resourceSize"), "id": a.get("id")}
        out.append(item)
        if out_dir is None:
            continue
        if why := _skip_reason(r, client.config):
            item["skipped"] = why
            continue
        name = _unique(item["name"], used)  # reserved before the download, so a failure never renames the rest
        sep = "&" if "?" in r["url"] else "?"
        try:
            data = client.get_bytes(f"{r['url']}{sep}api-version={client.config.api_version}", MAX_BYTES)
            item["path"] = str(_write(out_dir, name, data))
        except (AdoError, OSError) as e:  # per-file isolation, as in attach
            item["error"] = str(e)
    if out_dir is not None and out_dir.is_dir():
        comments.warn_unless_ignored(out_dir)
    return out
