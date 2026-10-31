"""Stack detection (`ado stack detect`, V3 piece 2, D§4): which stacks a repository holds, from the files tracked at a commit.

Process help, not a security boundary. Every path comes from the repository (in CI, from a PR), so it is untrusted text: it is only
ever matched against fixed marker names and printed through `safe_path`. Stack ids, evidence and hints come from the marker table and
fixed vocabularies, never from file contents; the few manifests that are read (from the commit's blobs, never the working tree) only
set booleans and fixed words."""
import fnmatch
import json
import os
import re

MAX_CANDIDATES = 20000  # marker-named paths at depth <= 3 considered; more is refused (fail closed), never silently cut
MAX_MANIFEST = 1 << 20  # bytes: a larger manifest is not read
MAX_READS = 32          # manifests read per run, and (separately) pubspec.yaml files read to tell `flutter` from `dart`
MAX_EVIDENCE = 10       # evidence entries printed per stack
MAX_DEPTH = 3           # a marker file is at most 3 path segments deep
_SAFE = re.compile(r"[A-Za-z0-9._/-]{1,200}")
_HEX = re.compile(r"[0-9a-f]{40}")  # SHA-1 repositories only: a 64-hex (sha256) id is refused
_REGULAR = (b"100644", b"100755")
_E_GIT, _E_COMMIT, _E_MANY = "cannot read git history here", "cannot read the commit", "too many files to inspect"

# D§4 as data. `typescript` (tsconfig.json beside package.json) and `flutter` (a `sdk: flutter` line in pubspec.yaml) refine `node` and
# `dart` in `detect`; "force-app/" is a directory. A medium marker alone is reported, never counted as detected.
MARKERS = (
    {"id": "python", "high": ("pyproject.toml",), "medium": ("setup.py", "setup.cfg", "requirements*.txt", "Pipfile")},
    {"id": "node", "high": ("package.json",), "medium": ()},
    {"id": "dart", "high": ("pubspec.yaml",), "medium": ()},
    {"id": "salesforce", "high": ("sfdx-project.json",), "medium": ("force-app/",)},
    {"id": "java", "high": ("pom.xml", "build.gradle", "build.gradle.kts"), "medium": ()},
    {"id": "go", "high": ("go.mod",), "medium": ()},
    {"id": "rust", "high": ("Cargo.toml",), "medium": ()},
    {"id": "dotnet", "high": ("*.csproj", "*.fsproj", "*.sln"), "medium": ()},
    {"id": "c-cpp", "high": ("CMakeLists.txt", "meson.build"), "medium": ("configure.ac", "Makefile")},
)
# files that are not markers but feed a hint (read from the tree, or only their presence)
_HINT_FILES = ("tsconfig.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pytest.ini", "conftest.py")
_PATTERNS = tuple(sorted({p for m in MARKERS for c in ("high", "medium") for p in m[c] if not p.endswith("/")} | set(_HINT_FILES)))
_LOCKS = (("package-lock.json", "npm"), ("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"))
_DEPS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
_TOKEN = re.compile(r"[A-Za-z0-9_.+-]+")
_FLUTTER = re.compile(r"^\s+sdk:\s*['\"]?flutter['\"]?\s*(#.*)?$")


def safe_path(p, n):
    """`p` when it is 1 to 200 characters of `[A-Za-z0-9._/-]`, else the placeholder `<path #n>`: the only way a repo path is printed."""
    return p if _SAFE.fullmatch(p) else f"<path #{n}>"


def _join(root, name):
    return name if root == "." else f"{root}/{name}"


def _media_name(n):
    n = n.lower()
    return n in ("ffmpeg", "av", "ffmpeg-python", "gstreamer") or n.startswith(("libav", "opencv"))


def _lines(text):
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


def _has_media(text):
    return any(_media_name(t) for ln in _lines(text) for t in _TOKEN.findall(ln))


def detect(repo=".", commit="HEAD"):
    """The stacks of `repo` at `commit` (HEAD, a full 40-hex commit id or a branch; never a tag):
    `{"outcome": "detected"|"multiple"|"unknown", "stacks": [{"id", "root", "confidence", "evidence", "hints"}], "domain_hints": [...]}`
    (plus `"note": "commit first"` when the repository has no commit). ValueError with a fixed text when git cannot answer."""
    from . import strict  # local, as config.py does: strict imports its siblings

    def run(*args):  # strict's hardened git (no replace refs, selectors, pager or prompt) with the user's global config: safe.directory works
        return strict._out(repo, *args, timeout=60)

    if run("rev-parse", "--git-dir") is None:
        raise ValueError(_E_GIT)
    if commit == "HEAD":
        full = strict._rev(repo, "HEAD")
        if not full:
            return {"outcome": "unknown", "stacks": [], "domain_hints": [], "note": "commit first"}
    else:
        full = strict._resolve_base(repo, commit) if _SAFE.fullmatch(commit) and not commit.startswith("-") and ".." not in commit else ""
    if not _HEX.fullmatch(full or ""):
        raise ValueError(_E_COMMIT)
    # shortcut: the whole listing is held in memory (git's own output size); upgrade to a streamed read if a repo needs it
    tree = run("ls-tree", "-r", "-z", "-l", "--full-tree", "--end-of-options", full)
    if tree is None:
        raise ValueError(_E_GIT)

    by_root, force_roots, src_roots, count = {}, set(), set(), 0
    for ent in tree.split(b"\0"):
        meta, _, raw = ent.partition(b"\t")
        f = meta.split()
        if len(f) != 4 or f[1] != b"blob" or not _HEX.fullmatch(f[2].decode("ascii", "replace")):
            continue
        segs = raw.decode("utf-8", "surrogateescape").split("/")
        for k in range(min(len(segs) - 1, MAX_DEPTH)):  # a directory named at depth k <= 2: its root is the path above it
            if segs[k] == "force-app":
                force_roots.add("/".join(segs[:k]) or ".")
            elif segs[k] == "src" and len(segs) > k + 2:
                src_roots.add("/".join(segs[:k]) or ".")
        name = segs[-1]
        if len(segs) <= MAX_DEPTH and f[0] in _REGULAR + (b"120000",) and any(fnmatch.fnmatchcase(name, p) for p in _PATTERNS):
            count += 1
            if count > MAX_CANDIDATES:
                raise ValueError(_E_MANY)
            by_root.setdefault("/".join(segs[:-1]) or ".", {})[name] = (f[0], f[2].decode("ascii"), f[3])

    cands = {}  # (id, root) -> {"high": [names], "medium": [names]}
    for root, names in by_root.items():
        for m in MARKERS:
            for conf in ("high", "medium"):
                hit = sorted(n for n in names if any(fnmatch.fnmatchcase(n, p) for p in m[conf] if not p.endswith("/")))
                if hit:
                    cands.setdefault((m["id"], root), {"high": [], "medium": []})[conf] += hit
    for root in force_roots:
        cands.setdefault(("salesforce", root), {"high": [], "medium": []})["medium"].append("force-app/")

    reads, used, incomplete, pkgs = {}, {"pubspec": 0, "other": 0}, [False], {}

    def read(root, name):
        """Text of a regular, small manifest at the commit, else None (a symlink and an oversized blob are never read). A read the
        budget or git refuses marks the result `hints incomplete`; the pubspec.yaml reads that decide dart/flutter have their own budget."""
        ent = by_root.get(root, {}).get(name)
        if ent is None or ent[0] not in _REGULAR or not ent[2].isdigit() or int(ent[2]) > MAX_MANIFEST:
            return None
        if (root, name) not in reads:
            kind = "pubspec" if name == "pubspec.yaml" else "other"
            out = run("cat-file", "blob", ent[1]) if used[kind] < MAX_READS else None
            used[kind] += 1
            incomplete[0] |= out is None
            reads[(root, name)] = None if out is None else out.decode("utf-8", "replace")
        return reads[(root, name)]

    def _no_duplicates(pairs):
        if len({k for k, _ in pairs}) != len(pairs):
            raise ValueError("duplicate key")
        return dict(pairs)

    def package(root):
        """The parsed package.json, or {} when absent or unreadable; one that is present but not a JSON object without duplicate keys
        marks the result `hints incomplete` (fail closed: hints false, never a guess)."""
        if root not in pkgs:
            text, pkgs[root] = read(root, "package.json"), {}
            if text is not None:
                try:
                    data = json.loads(text, object_pairs_hook=_no_duplicates)
                except (ValueError, RecursionError):
                    data = None
                if isinstance(data, dict):
                    pkgs[root] = data
                else:
                    incomplete[0] = True
        return pkgs[root]

    def dep_names(root):
        return {k for d in _DEPS if isinstance(package(root).get(d), dict) for k in package(root)[d]}

    high = {k for k, v in cands.items() if v["high"]}
    for (sid, root) in sorted(k for k in high if k[0] == "salesforce"):
        cands.pop(("node", root), None)  # the package.json beside sfdx-project.json is its LWC Jest harness
    for (sid, root) in sorted(k for k in high if k[0] == "dart"):
        prefix = _join(root, "android")
        for k in [k for k in cands if k[0] == "java" and (k[1] == prefix or k[1].startswith(prefix + "/"))]:
            cands.pop(k)  # a Flutter or Dart app's Android host project is part of it
    stacks, domain, kept = [], set(), {}
    for (sid, root) in sorted(cands, key=lambda k: (k[1] != ".", k[1], k[0])):
        c, names, hints = cands[(sid, root)], by_root.get(root, {}), {}
        evidence = c["high"] + c["medium"]
        if sid == "node" and c["high"] and "tsconfig.json" in names:
            sid, evidence = "typescript", c["high"] + ["tsconfig.json"] + c["medium"]
        evidence = [_join(root, e) for e in evidence]
        if sid == "dart" and c["high"] and any(_FLUTTER.match(ln) for ln in (read(root, "pubspec.yaml") or "").splitlines()):
            sid = "flutter"
        scan = []  # manifests whose lines are scanned for the media hint
        if sid == "python":
            reqs = sorted(n for n in names if fnmatch.fnmatchcase(n, "requirements*.txt"))
            pyproject = read(root, "pyproject.toml") or ""
            texts = [pyproject] + [read(root, n) or "" for n in reqs + ["setup.cfg", "Pipfile"] if n in names]
            req_pytest = any(re.match(r"\s*pytest(?![\w.-])", ln, re.I) for t in texts[1:] for ln in _lines(t))
            hints = {"pytest": any(ln.strip().startswith("[tool.pytest") for ln in pyproject.splitlines()) or req_pytest
                     or "pytest.ini" in names or "conftest.py" in names or "conftest.py" in by_root.get(_join(root, "tests"), {}),
                     "src_layout": root in src_roots}
            scan = texts
        elif sid in ("node", "typescript"):
            locks = {tool for lock, tool in _LOCKS if lock in names}
            hints = {**({"package_manager": next(iter(locks)) if len(locks) == 1 else "ambiguous"} if locks else {}),
                     "test_script": isinstance(package(root).get("scripts"), dict) and bool(package(root)["scripts"].get("test"))}
            if any(d.startswith("@pega/") for d in dep_names(root)):
                hints["pega"] = True
                domain.add("pega")
            if any(_media_name(d) for d in dep_names(root)):
                domain.add("media")
        elif sid == "salesforce":
            hints = {"lwc_jest": "@salesforce/sfdx-lwc-jest" in dep_names(root)}
        elif sid == "java":
            tools = {"maven" for n in names if n == "pom.xml"} | {"gradle" for n in names if n.startswith("build.gradle")}
            hints = {"build_tool": "both" if len(tools) == 2 else next(iter(tools))} if tools else {}
        elif sid == "c-cpp":
            scan = [read(root, "CMakeLists.txt") or ""]
        if any(_has_media(t) for t in scan):
            domain.add("media")
        s = {"id": sid, "root": root, "confidence": "high" if c["high"] else "medium", "evidence": evidence, "hints": hints}
        family = {"typescript": "node", "flutter": "dart"}.get(sid, sid)
        parent = None  # a nested root of the same stack (workspace, module, crate) joins its nearest ancestor: one stack, more evidence
        if s["confidence"] == "high":
            parts = root.split("/")
            ancestors = ["/".join(parts[:k]) for k in range(len(parts) - 1, 0, -1)] + ["."] if root != "." else []
            parent = next((kept[(family, a)] for a in ancestors if (family, a) in kept), None)
            if parent is None:
                kept[(family, root)] = s
        if parent is None:
            stacks.append(s)
        else:
            parent["evidence"] += evidence
            parent["hints"].update({k: v for k, v in hints.items() if k == "pega" and v})
    n = 0

    def printable(p):
        nonlocal n
        n += 1
        return safe_path(p, n)

    for s in stacks:
        root = s["root"]
        s["root"] = printable(root)
        s["evidence"] = [printable(e) for e in s["evidence"][:MAX_EVIDENCE]]
    highs = sum(s["confidence"] == "high" for s in stacks)
    return {"outcome": "unknown" if not highs else "detected" if highs == 1 else "multiple", "stacks": stacks,
            "domain_hints": sorted(domain), **({"note": "hints incomplete"} if incomplete[0] else {})}
