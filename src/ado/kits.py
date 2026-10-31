"""Kit digests and `ado kits verify` (local, read-only): which Spec Kit extensions are installed, what a human pinned in
`ado.kits.lock.json`, and whether the kit's files and the agent copies Spec Kit registered still match. Everything read from the repo
(the `.registry`, kit files, agent copies, the lockfile) is hostile: no symlink is followed, reads are confined and capped, a path with a
control character is refused before any hashing, and errors and findings are fixed texts that carry no kit text. Process enforcement,
not a security boundary: a local digest says "unchanged since a human looked", not "safe"."""
import hashlib
import os
import re
import stat

from . import stack, strict
from .config import ConfigInvalid, _has_control

LOCK_FILE = "ado.kits.lock.json"
REGISTRY = ".specify/extensions/.registry"
MAX_REGISTRY = 1 << 20   # bytes
MAX_FILE = 16 << 20      # bytes per kit file or agent copy; larger is refused (fail closed)
MAX_FILES = 5000         # kit files hashed per kit
_REASONS = ("symlink", "unreadable", "empty", "control-char", "attributes")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")  # a registered command or skill name: one path component
_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BLOB = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_CHANGED = "changed"  # dir_digest_at's answer for a symlink, submodule, odd mode or odd path: never equal to a pinned digest
FIX = "review the kit, then have a human pin it from a terminal"


class KitError(Exception):
    """A kit input that cannot be trusted; `reason` is one of symlink, unreadable, empty, control-char, attributes (the message is that word only)."""

    def __init__(self, reason):
        assert reason in _REASONS
        super().__init__(reason)
        self.reason = reason


def _stat(root, rel):
    """lstat of `rel` (a repo-relative path) with every component checked: a symlink anywhere on the way is KitError("symlink"),
    an entry that does not exist is FileNotFoundError, anything else KitError("unreadable")."""
    path, st = str(root), None
    for part in rel.split("/"):
        path = os.path.join(path, part)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise
        except OSError:
            raise KitError("unreadable") from None
        if stat.S_ISLNK(st.st_mode):
            raise KitError("symlink")
    return st


def _read_regular(root, rel, cap, missing_ok=False):
    """The bytes of regular file `rel` under `root` (no symlink, at most `cap` bytes); None when absent and `missing_ok`."""
    try:
        st = _stat(root, rel)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise KitError("unreadable") from None
    if not stat.S_ISREG(st.st_mode) or st.st_size > cap:
        raise KitError("unreadable")
    try:
        fd = os.open(os.path.join(str(root), rel), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise KitError("unreadable") from None
    with os.fdopen(fd, "rb") as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise KitError("unreadable")
    return data


def _check_id(kit_id):
    if not (isinstance(kit_id, str) and stack._ID.fullmatch(kit_id)):
        raise KitError("unreadable")
    return kit_id


def _names(v):
    if v is None:
        return []
    if not (isinstance(v, list) and all(isinstance(n, str) and _NAME.fullmatch(n) for n in v)):
        raise ValueError
    return list(v)


def installed(root="."):
    """The kits in `.specify/extensions/.registry`: `[{"id", "enabled", "version", "registered": {"commands", "skills"}}]` in registry
    order, `ado` left out. A missing `enabled` is True (as upstream). An entry whose id fails `[a-z0-9-]{1,40}` or whose fields are
    malformed becomes `{"id": None, "n": <1-based position>}` and is reported unpinned, never skipped. No registry: `[]`. A registry that
    is a symlink, not a regular file, over 1 MiB, not valid JSON, has a duplicate key or the wrong shape: KitError (fail closed)."""
    raw = _read_regular(root, REGISTRY, MAX_REGISTRY, missing_ok=True)
    if raw is None:
        return []
    try:
        data = stack.parse_json(raw, MAX_REGISTRY)
    except ValueError:
        raise KitError("unreadable") from None
    if not (isinstance(data, dict) and isinstance(data.get("extensions"), dict)):
        raise KitError("unreadable")
    out = []
    for n, (kit_id, e) in enumerate(data["extensions"].items(), 1):
        if kit_id == "ado":
            continue
        try:
            if not (isinstance(kit_id, str) and stack._ID.fullmatch(kit_id) and isinstance(e, dict)):
                raise ValueError
            enabled = e.get("enabled", True)
            rc = e.get("registered_commands", {})
            if not (isinstance(enabled, bool) and isinstance(rc, dict)):
                raise ValueError
            version = e.get("version")
            out.append({"id": kit_id, "enabled": enabled, "version": version if isinstance(version, str) and _VERSION.fullmatch(version) else None,
                        "registered": {"commands": _names(rc.get("claude")), "skills": _names(e.get("registered_skills"))}})
        except ValueError:
            out.append({"id": None, "n": n})
    return out


def _digest(entries):
    """sha256 over sorted `<blob id>  <relpath>` lines (blob ids are SHA-1 in SHA-1 repositories)."""
    h = hashlib.sha256()
    for blob, rel in sorted(entries, key=lambda t: t[1].encode("utf-8")):
        h.update(f"{blob}  {rel}\n".encode("utf-8"))
    return h.hexdigest()


def _excluded(kit_id, rel):
    """Project settings the user edits, not vetted: only at the top of the kit dir."""
    return "/" not in rel and (rel == "local-config.yml" or rel.endswith("-config.local.yml") or rel == f"{kit_id}-config.yml")


def _paths(raw, prefix):
    """The NUL-separated paths in `raw` as text; a path that is not UTF-8 is unreadable, one with a control character is refused."""
    out = []
    for b in raw.split(b"\0"):
        if not b:
            continue
        try:
            text = b.decode("utf-8")
        except UnicodeDecodeError:
            raise KitError("unreadable") from None
        if _has_control(text):
            raise KitError("control-char")
        if not text.startswith(prefix):
            raise KitError("unreadable")
        out.append(text)
    return out


def dir_digest(root, kit_id):
    """The digest of the regular files git tracks or would track under `.specify/extensions/<id>/` in the working tree (same set as the
    commit once committed), minus local and kit config files. KitError: symlink or non-regular file, unreadable, control-char path,
    empty set. Blob ids come from `git hash-object --stdin-paths`, so CRLF/`core.autocrlf` give the committed value."""
    prefix = f".specify/extensions/{_check_id(kit_id)}/"
    _stat_dir(root, prefix[:-1])
    listed = strict._git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--end-of-options", "--", prefix)
    if listed is None or listed.returncode:
        raise KitError("unreadable")
    paths = sorted({p for p in _paths(listed.stdout, prefix) if not _excluded(kit_id, p[len(prefix):])})  # all checked before git hashes
    if not paths:
        raise KitError("empty")
    if len(paths) > MAX_FILES:
        raise KitError("unreadable")
    for p in paths:
        try:
            st = _stat(root, p)
        except FileNotFoundError:
            raise KitError("unreadable") from None
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
            raise KitError("unreadable")
    _refuse_content_attributes(root, paths)
    hashed = strict._git(root, "hash-object", "--stdin-paths", stdin="".join(p + "\n" for p in paths).encode("utf-8"), timeout=120)
    ids = hashed.stdout.decode("ascii", "replace").split("\n")[:-1] if hashed is not None and not hashed.returncode else []
    if len(ids) != len(paths) or not all(_BLOB.fullmatch(i) for i in ids):
        raise KitError("unreadable")
    return _digest(zip(ids, (p[len(prefix):] for p in paths)))


def _refuse_content_attributes(root, paths):
    """KitError("attributes") when `.gitattributes` (or any attributes source) sets `ident`, `filter` or `working-tree-encoding` on a kit
    path: `hash-object` would run that rewrite, so the local digest could differ from the committed one (or run a filter driver).
    `text` and `eol` stay allowed: they are what makes CRLF and `core.autocrlf` agree with the commit."""
    r = strict._git(root, "check-attr", "-z", "--stdin", "ident", "filter", "working-tree-encoding", stdin="\0".join(paths).encode("utf-8") + b"\0")
    fields = r.stdout.split(b"\0")[:-1] if r is not None and not r.returncode else None
    if fields is None or len(fields) != 9 * len(paths):
        raise KitError("unreadable")
    if any(v not in (b"unspecified", b"unset") for v in fields[2::3]):
        raise KitError("attributes")


def _stat_dir(root, rel):
    try:
        st = _stat(root, rel)
    except FileNotFoundError:
        raise KitError("empty") from None
    if not stat.S_ISDIR(st.st_mode):
        raise KitError("unreadable")


def dir_digest_at(repo, commit, kit_id):
    """The same digest from the files of `commit` (a 40-hex id; ValueError otherwise) that git tracks under the kit dir: what CI sees.
    None when the commit tracks no (non-excluded) file there; "changed" when an entry is a symlink or submodule (mode 120000/160000),
    any other non-regular mode or a path with a control character; KitError("unreadable") when git cannot answer."""
    if not (isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}", commit)):
        raise ValueError("commit must be a resolved 40-hex commit id")
    prefix = f".specify/extensions/{_check_id(kit_id)}/"
    listed = strict._git(repo, "ls-tree", "-r", "-z", "--full-tree", "--end-of-options", commit, "--", prefix)
    if listed is None or listed.returncode:
        raise KitError("unreadable")
    entries = []
    for rec in listed.stdout.split(b"\0"):
        if not rec:
            continue
        meta, _, raw_path = rec.partition(b"\t")
        fields = meta.decode("ascii", "replace").split(" ")
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            return _CHANGED
        if len(fields) != 3 or not path.startswith(prefix):
            raise KitError("unreadable")
        rel = path[len(prefix):]
        if _has_control(path):
            return _CHANGED
        if _excluded(kit_id, rel):
            continue
        if fields[0] not in ("100644", "100755") or fields[1] != "blob" or not _BLOB.fullmatch(fields[2]):
            return _CHANGED
        entries.append((fields[2], rel))
    return _digest(entries) if entries else None


def _agent_files(root, registered):
    """The Claude copies Spec Kit registered, by the exact registered names (never a glob: `sf` vs `sf-extra`). A skill name is
    `.claude/skills/<name>/SKILL.md`; a command name `speckit.<a>.<b>` is the skill `speckit-<a>-<b>` when that exists (Spec Kit's
    skills layout, what a current install writes), else `.claude/commands/<name>.md`."""
    paths = set()
    for name in registered.get("skills", []):
        paths.add(f".claude/skills/{name}/SKILL.md")
    for name in registered.get("commands", []):
        skill = ".claude/skills/speckit-" + (name[len("speckit."):] if name.startswith("speckit.") else name).replace(".", "-") + "/SKILL.md"
        try:
            _stat(root, skill)
            paths.add(skill)
        except FileNotFoundError:
            paths.add(f".claude/commands/{name}.md")
    return sorted(paths)


def agents_sha256(root, registered):
    """sha256 over `<sha256 of the bytes>  <relpath>` lines of the agent copies named in `registered` ({"commands", "skills"}). A symlink
    (a `--dev` install), a missing, non-regular, oversized or unreadable copy is KitError."""
    for name in (*registered.get("commands", []), *registered.get("skills", [])):
        if not (isinstance(name, str) and _NAME.fullmatch(name)):
            raise KitError("unreadable")
    h = hashlib.sha256()
    for rel in _agent_files(root, registered):
        h.update(f"{hashlib.sha256(_read_regular(root, rel, MAX_FILE)).hexdigest()}  {rel}\n".encode("utf-8"))
    return h.hexdigest()


def read_lock(raw):
    """The lockfile in `raw` (bytes) as `{"schema": 1, "kits": {id: {version, source, catalog_sha256, dir_digest, agents_sha256}}}`.
    Invalid JSON, a duplicate key, another schema, a bad kit id, a non-object entry or a digest that is not 64 lowercase hex characters
    (`catalog_sha256` may be null) is ConfigInvalid with a fixed text. `source` that is not an acceptable https URL and `version` that
    is not `[0-9A-Za-z.+-]{1,40}` are stored as null."""
    why = stack.lock_error(raw)
    if why:
        raise ConfigInvalid(f"{LOCK_FILE} {why}")
    kits = {}
    for n, (kit_id, e) in enumerate(stack.parse_json(raw, stack.MAX_PROFILE)["kits"].items(), 1):
        if not (isinstance(e, dict) and all(isinstance(e.get(k), str) and _HEX64.fullmatch(e[k]) for k in ("dir_digest", "agents_sha256"))
                and (e.get("catalog_sha256") is None or (isinstance(e["catalog_sha256"], str) and _HEX64.fullmatch(e["catalog_sha256"])))):
            raise ConfigInvalid(f"{LOCK_FILE}: kit #{n} is not an object with 64-hex `dir_digest` and `agents_sha256`")
        src, ver = e.get("source"), e.get("version")
        kits[kit_id] = {"version": ver if isinstance(ver, str) and _VERSION.fullmatch(ver) else None,
                        "source": src if isinstance(src, str) and len(src) <= stack.CAP_URL and stack._url_error(src) is None else None,
                        "catalog_sha256": e.get("catalog_sha256"), "dir_digest": e["dir_digest"], "agents_sha256": e["agents_sha256"]}
    return {"schema": 1, "kits": kits}


def load_lock(root="."):
    """`read_lock` of the working-tree `ado.kits.lock.json` (regular file, no symlink, at most 256 KiB); absent: no kit is pinned."""
    raw = _read_regular(root, LOCK_FILE, stack.MAX_PROFILE, missing_ok=True)
    return {"schema": 1, "kits": {}} if raw is None else read_lock(raw)


def _matches(root, kit, pinned):
    """(True, None) when the kit's files, agent copies and version agree with the lock, else (False, reason or None)."""
    try:
        ok = (dir_digest(root, kit["id"]) == pinned["dir_digest"] and agents_sha256(root, kit["registered"]) == pinned["agents_sha256"]
              and (pinned["version"] is None or pinned["version"] == kit["version"]))
        return ok, None
    except KitError as e:
        return False, e.reason


def verify(root, lock):
    """`{"kits": [{"id", "state"[, "reason"]}], "findings": [{"rule", "kit", "fix"}]}` for the kits in the registry against
    `lock` (from `read_lock`): `pinned`; `unpinned` (not in the lock, or an id/entry that could not be read: `id` null with `n`);
    `changed` (a hash or the version differs, or a file could not be read: `reason` names why); `stale` (locked but not installed).
    A disabled kit is judged like an enabled one: disabling only flips `enabled`, the agent copies stay invocable. Findings are `kit-unpinned` and `kit-changed`; the texts are fixed and carry no kit text."""
    out, findings, seen = [], [], set()
    for kit in installed(root):
        if kit["id"] is None:
            out.append({"id": None, "n": kit["n"], "state": "unpinned"})
            findings.append({"rule": "kit-unpinned", "kit": f"#{kit['n']}", "fix": FIX})
            continue
        seen.add(kit["id"])
        pinned = lock["kits"].get(kit["id"])
        if pinned is None:
            out.append({"id": kit["id"], "state": "unpinned"})
            findings.append({"rule": "kit-unpinned", "kit": kit["id"], "fix": FIX})
            continue
        ok, reason = _matches(root, kit, pinned)
        row = {"id": kit["id"], "state": "pinned" if ok else "changed"}
        if reason:
            row["reason"] = reason
        out.append(row)
        if not ok:
            findings.append({"rule": "kit-changed", "kit": kit["id"], "fix": FIX})
    out += [{"id": k, "state": "stale"} for k in lock["kits"] if k not in seen]
    out.sort(key=lambda r: (r["id"] is None, r["id"] or "", r.get("n", 0)))
    return {"kits": out, "findings": findings}
