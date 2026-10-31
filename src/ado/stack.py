"""Stack detection (`ado stack detect`, D§4) and profile validation (`stack.validate`, D§5), V3 piece 2.

Process help, not a security boundary. Every path comes from the repository (in CI, from a PR), so it is untrusted text: it is only
ever matched against fixed marker names and printed through `safe_path`. Stack ids, evidence and hints come from the marker table and
fixed vocabularies, never from file contents; the few manifests that are read (from the commit's blobs, never the working tree) only
set booleans and fixed words."""
import datetime
import fnmatch
import json
import os
import re
import urllib.parse

from .config import _has_control

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


# ---- the stack profile (`ado.stack.json`, D§5): validation. The profile is attacker-controlled in CI and model-written at creation, so
# `validate` is a trust boundary: a whitelist schema, a cap on every size and count, and FIXED error texts that name the rule and the
# position (`stacks[2].commands.test.run`, `<key #3>` for an unknown key), never a value. Process help, not a sandbox. Commands in a profile are shell code that a human approves;
# the validator does not make them safe.

PROFILE_FILE = "ado.stack.json"
MAX_PROFILE = 256 * 1024  # bytes
MAX_STACKS, MAX_CONVENTIONS, MAX_DOD, MAX_SOURCES, MAX_COMMANDS, MAX_DETECTED_EVIDENCE, MAX_ERRORS = 8, 50, 30, 100, 20, 20, 100
CAP_COMMAND, CAP_TEXT, CAP_URL, CAP_ROOT = 500, 300, 500, 200

# copy of scripts/scrub_check.py SECRETS (that script runs standalone, so it is copied, not moved); tests/test_scrub_check.py pins equality
SECRETS = [
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("ado-pat", re.compile(r"\b[a-z0-9]{52}\b")),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
_ID = re.compile(r"[a-z0-9-]{1,40}")
_ROOT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*(/[A-Za-z0-9_][A-Za-z0-9._-]*)*")
_SRC = re.compile(r"S[1-9][0-9]{0,3}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_URL_CHARS = re.compile(r"[\x21-\x7e]+")
_SECRET_KEY = re.compile(r"token|key|sig|password|secret", re.I)
_PRINTABLE = re.compile(r"[\x20-\x7e]+")  # a command is one line of printable ASCII: no look-alike letters, joiners or direction marks
# a tripwire, not a sandbox: it catches the obvious fetch-and-run shapes (a word list, easy to get around: `| zsh`, `bash <(...)`)
_TRIPWIRE = re.compile(r"\b(?:sudo|curl|wget|eval)\b|invoke-webrequest|\|\s*(?:sh|bash)\b|`|\$\(", re.I)
_COMMANDS = ("test", "lint", "format_check", "build", "coverage")
_COMMAND_LISTS = ("security", "org_only")
_E_PARSE = "the profile is not valid JSON"


class _DuplicateKey(ValueError):
    pass


def parse_json(raw, cap):
    """The JSON value in `raw` (bytes, at most `cap` bytes, UTF-8, no duplicate object keys, no NaN/Infinity and no number that overflows
    to infinity, such as 1e400). ValueError with a fixed text otherwise; the text never carries input."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("the profile is not bytes")
    if len(raw) > cap:
        raise ValueError(f"the profile is larger than {cap} bytes")
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("the profile is not valid UTF-8") from None

    def pairs(items):
        if len({k for k, _ in items}) != len(items):
            raise _DuplicateKey()
        return dict(items)

    def constant(_):
        raise ValueError()

    def number(raw_number):
        value = float(raw_number)
        if value in (float("inf"), float("-inf")):
            raise ValueError()
        return value

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)
    except _DuplicateKey:
        raise ValueError("the profile has a duplicate key") from None
    except (ValueError, RecursionError):
        raise ValueError(_E_PARSE) from None


def _url_error(u):
    """A fixed rule text when `u` is not an acceptable https documentation URL, else None."""
    if not _URL_CHARS.fullmatch(u) or "\\" in u:
        return "must be printable ASCII without spaces or backslashes"
    try:
        p = urllib.parse.urlsplit(u)
        p.port  # noqa: B018  (raises ValueError on a bad port)
    except ValueError:
        return "is not a valid URL"
    if p.scheme != "https":
        return "must use https"
    if "@" in p.netloc:
        return "must not contain user information"
    if not p.hostname:
        return "must have a host"
    for part, need_eq in ((p.query, False), (p.fragment, True)):
        for kv in re.split(r"[&;]", part):
            if (not need_eq or "=" in kv) and _SECRET_KEY.search(urllib.parse.unquote(urllib.parse.unquote(kv.partition("=")[0]))):
                return "must not carry a token, key, signature, password or secret parameter"
    return None


def _today():
    return max(datetime.date.today(), datetime.datetime.now(datetime.timezone.utc).date())  # the later: no timezone makes today "future"


def validate(data):
    """Every D§5 rule on a parsed profile: `[{"path": str, "error": str}]`, empty when valid. The errors are fixed texts (rule + position
    in the profile, an unknown key as `<key #n>`), never a value from the profile.

    A valid profile is NOT a safe one: a command in it is shell code that a human approves by reading it, and this validator does not make
    it safe. The checks (printable ASCII, one line, the tripwire word list) only catch accidents, look-alike text and the obvious
    fetch-and-run lines copied from the web; they are not a sandbox."""
    errs, total = [], [0]

    def bad(path, msg):
        total[0] += 1
        if len(errs) < MAX_ERRORS:
            errs.append({"path": path, "error": msg})

    def j(path, key):
        return f"{path}.{key}" if path else key

    def obj(v, path, required, optional=()):
        """`v` when it is an object whose keys are known (an unknown key is named by position), else None."""
        if not isinstance(v, dict):
            bad(path or "profile", "must be an object")
            return None
        for n, k in enumerate(v, 1):
            if k not in required and k not in optional:
                bad(j(path, f"<key #{n}>"), "unknown key")
        for k in required:
            if k not in v:
                bad(j(path, k), "is required")
        return v

    def items(v, path, cap):
        if not isinstance(v, list):
            bad(path, "must be a list")
        elif len(v) > cap:
            bad(path, f"has more than {cap} entries")
        else:
            return v
        return []

    def text(v, path, cap=CAP_TEXT):
        """True when `v` is a non-empty string within the cap, without control characters or a secret pattern."""
        if not isinstance(v, str) or not v:
            bad(path, "must be a non-empty string")
        elif len(v) > cap:
            bad(path, f"is longer than {cap} characters")
        elif _has_control(v):
            bad(path, "contains a control character")
        elif any(pat.search(v) for _, pat in SECRETS):
            bad(path, "matches a secret pattern")
        else:
            return True
        return False

    def date(v, path):
        try:
            ok = isinstance(v, str) and _DATE.fullmatch(v) and datetime.date.fromisoformat(v)
        except ValueError:
            ok = False
        if not ok:
            bad(path, "must be an ISO date (YYYY-MM-DD)")
        elif ok > _today():
            bad(path, "is in the future")

    top = obj(data, "", ("schema", "version", "status", "stacks"), ("sources", "approval"))
    if top is None:
        return errs
    if "schema" in top and (type(top["schema"]) is not int or top["schema"] != 1):  # exactly the int 1: not 1.0, not True
        bad("schema", "must be 1")
    v = top.get("version")
    if "version" in top and (not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 1_000_000):
        bad("version", "must be a positive integer")
    if "status" in top and top["status"] not in ("draft", "approved"):
        bad("status", "must be draft or approved")

    ids = set()  # the source ids that are defined (valid and present)
    srcs = top.get("sources", {})
    if not isinstance(srcs, dict):
        bad("sources", "must be an object")
    elif len(srcs) > MAX_SOURCES:
        bad("sources", f"has more than {MAX_SOURCES} entries")
    else:
        for n, (k, s) in enumerate(srcs.items(), 1):
            if not isinstance(k, str) or not _SRC.fullmatch(k):
                bad(f"sources.<key #{n}>", "unknown key")  # the key is user text: named by position
                continue
            ids.add(k)
            p = f"sources.{k}"
            s = obj(s, p, ("kind", "retrieved", "evidence"), ("url", "path"))
            if s is None:
                continue
            kind = s.get("kind")
            if "kind" in s and kind not in ("official", "community", "implementation", "repo"):
                bad(j(p, "kind"), "must be official, community, implementation or repo")
            if "retrieved" in s:
                date(s["retrieved"], j(p, "retrieved"))
            if "evidence" in s:
                text(s["evidence"], j(p, "evidence"))
            for f, cap in (("url", CAP_URL), ("path", CAP_TEXT)):
                if s.get(f) is not None and text(s[f], j(p, f), cap) and f == "url":
                    e = _url_error(s[f])
                    if e:
                        bad(j(p, f), e)
            if kind == "repo" and s.get("path") is None:
                bad(j(p, "path"), "is required for a repo source")
            if kind in ("official", "community", "implementation") and s.get("url") is None:
                bad(j(p, "url"), "is required for this source kind")

    def ref(v, path):
        if v == "unverified":
            return
        if not isinstance(v, str) or not _SRC.fullmatch(v):
            bad(path, "must be a source id (S<n>) or unverified")
        elif v not in ids:
            bad(path, "refers to a source that is not defined")

    def command(v, path):
        if (v := obj(v, path, ("run",), ("source", "why"))) is None:
            return
        run = v.get("run", "")
        if "run" not in v:
            pass  # reported as required
        elif run is None:
            if "why" not in v:
                bad(j(path, "why"), "is required when run is null")
        elif isinstance(run, str) and run and not _PRINTABLE.fullmatch(run):
            bad(j(path, "run"), "must be one line of printable ASCII")
        elif text(run, j(path, "run"), CAP_COMMAND) and _TRIPWIRE.search(run):
            bad(j(path, "run"), "contains a refused command token")
        if "why" in v:
            text(v["why"], j(path, "why"))
        if run is not None or "source" in v:
            ref(v.get("source"), j(path, "source"))

    def approval(a):
        a = obj(a, "approval", ("by", "date", "sha256"), ("decision",))
        if a is None:
            return
        for f in ("by", "decision"):
            if f in a:
                text(a[f], j("approval", f))
        if "date" in a:
            date(a["date"], "approval.date")
        if "sha256" in a and not (isinstance(a["sha256"], str) and _SHA256.fullmatch(a["sha256"])):
            bad("approval.sha256", "must be 64 lowercase hex digits")

    seen = set()
    for i, st in enumerate(items(top["stacks"], "stacks", MAX_STACKS) if "stacks" in top else ()):
        p = f"stacks[{i}]"
        if (st := obj(st, p, ("id", "root"), ("detected", "kit", "commands", "conventions", "dod"))) is None:
            continue
        sid = st.get("id")
        if "id" in st and not (isinstance(sid, str) and _ID.fullmatch(sid)):
            bad(j(p, "id"), "must be 1 to 40 characters of a-z, 0-9 and -")
        elif sid in seen:
            bad(j(p, "id"), "is a duplicate stack id")
        elif "id" in st:
            seen.add(sid)
        root = st.get("root")
        if "root" in st and not (isinstance(root, str) and len(root) <= CAP_ROOT and (root == "." or _ROOT.fullmatch(root))):
            bad(j(p, "root"), 'must be "." or "/"-separated segments of letters, digits, _, . and - that start with a letter, digit or _')
        elif isinstance(root, str) and any(pat.search(root) for _, pat in SECRETS):
            bad(j(p, "root"), "matches a secret pattern")
        if "kit" in st and st["kit"] is not None and not (isinstance(st["kit"], str) and _ID.fullmatch(st["kit"])):
            bad(j(p, "kit"), "must be null or 1 to 40 characters of a-z, 0-9 and -")
        if "detected" in st:
            d = obj(st["detected"], j(p, "detected"), ("confidence", "evidence"))
            if d is not None:
                if d.get("confidence") not in ("high", "medium") and "confidence" in d:
                    bad(j(p, "detected.confidence"), "must be high or medium")
                for n, e in enumerate(items(d.get("evidence", []), j(p, "detected.evidence"), MAX_DETECTED_EVIDENCE)):
                    text(e, j(p, f"detected.evidence[{n}]"))
        if "commands" in st:
            cmds = obj(st["commands"], j(p, "commands"), (), _COMMANDS + _COMMAND_LISTS)
            for k in () if cmds is None else cmds:
                c = j(p, f"commands.{k}")
                if k in _COMMAND_LISTS:
                    for n, v in enumerate(items(cmds[k], c, MAX_COMMANDS)):
                        command(v, f"{c}[{n}]")
                elif k in _COMMANDS and (cmds[k] is not None or k == "test"):
                    command(cmds[k], c)
        for key, field, cap in (("conventions", "rule", MAX_CONVENTIONS), ("dod", "item", MAX_DOD)):
            for n, e in enumerate(items(st.get(key, []), j(p, key), cap)):
                e = obj(e, f"{p}.{key}[{n}]", (field, "source"))
                if e is not None:
                    if field in e:
                        text(e[field], f"{p}.{key}[{n}].{field}")
                    if "source" in e:
                        ref(e["source"], f"{p}.{key}[{n}].source")
    if "approval" in top:
        approval(top["approval"])
    if total[0] > len(errs):
        errs.append({"path": "profile", "error": "further errors omitted"})
    return errs
