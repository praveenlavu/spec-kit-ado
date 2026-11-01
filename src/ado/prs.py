import json
import os
import re
import subprocess
import urllib.parse
from pathlib import Path, PurePosixPath

from . import dod, workitems
from .dod import _visible  # noqa: F401  (the one fence/comment blanker lives with the ledger sections)
from .push import _cell


def _repo_url(client, repo, tail):
    return (f"/{urllib.parse.quote(client.config.project)}/_apis/git/repositories/"
            f"{urllib.parse.quote(repo, safe='')}/{tail}?api-version={client.config.api_version}")


def _ref(branch):
    return branch if branch.startswith("refs/") else f"refs/heads/{branch}"


def with_ab_ref(title, work_item_id):
    token = f"AB#{work_item_id}"
    return title if re.search(rf"(?<![\w#]){re.escape(token)}(?!\w)", title) else f"{title} {token}"


AZURE_DESC_MAX = 4000  # Azure Repos rejects longer PR descriptions
_TRUNC = "\n… (truncated; full text in the ledger)"


def azure_description(body):
    return body if len(body) <= AZURE_DESC_MAX else body[:AZURE_DESC_MAX - len(_TRUNC)] + _TRUNC


def azure_create(client, repo, source_branch, target_branch, title, body, work_item_ids, draft=False):
    return client.post(_repo_url(client, repo, "pullrequests"), {
        "sourceRefName": _ref(source_branch), "targetRefName": _ref(target_branch),
        "title": title, "description": azure_description(body),
        "workItemRefs": [{"id": str(i)} for i in work_item_ids], **({"isDraft": True} if draft else {})})


def ensure_work_item_links(client, repo, pr, work_item_ids):
    """Azure accepts `workItemRefs` on create but has been seen to drop them (R3 B3). Read the PR's links back and add any
    missing story as a Pull Request ArtifactLink. Returns (linked ids, note or None); never raises: the PR already exists."""
    try:
        pr_id = int(pr["pullRequestId"])
        have = {str(w.get("id")) for w in client.get(_repo_url(client, repo, f"pullrequests/{pr_id}/workitems")).get("value") or []}
        repo_info = pr["repository"]
        url = f"vstfs:///Git/PullRequestId/{repo_info['project']['id']}%2F{repo_info['id']}%2F{pr_id}"
    except Exception as e:  # noqa: BLE001 - verification is best effort
        return [], f"could not verify that PR {pr.get('pullRequestId')} is linked to its work item ({type(e).__name__}); check its Development section"
    linked, note = [], None
    for wid in work_item_ids:
        if str(wid) in have:
            linked.append(wid)
            continue
        try:
            workitems.add_relation(client, wid, {"rel": "ArtifactLink", "url": url, "attributes": {"name": "Pull Request"}})
            linked.append(wid)
        except Exception as e:  # noqa: BLE001
            note = f"PR {pr_id} was not linked to work item {wid} and adding the link failed ({type(e).__name__}); link it by hand"
    return linked, note


def azure_comment(client, repo, pr_id, text):
    return client.post(_repo_url(client, repo, f"pullRequests/{int(pr_id)}/threads"),
                       {"comments": [{"parentCommentId": 0, "content": text, "commentType": 1}],
                        "status": 1})


def github_create_args(title, body_file, base, head, draft=False):
    return ["gh", "pr", "create", "--title", title, "--body-file", body_file,
            "--base", base, "--head", head] + (["--draft"] if draft else [])


def github_create(title, body, base, head, draft=False):
    """`body` is the text already read (and shown in the plan): it goes to `gh` on stdin, so a body file swapped after the approval is never read."""
    try:
        r = subprocess.run(github_create_args(title, "-", base, head, draft), input=body,
                           check=False, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        raise ValueError("`gh` (GitHub CLI) is not installed or not on PATH; install it or set repo_host to azure")
    return {"error": r.stderr} if r.returncode else {"url": r.stdout.strip()}


PREPARED_ARGS = ".ado/pr-args.json"
PREPARED_BODY = ".ado/pr-body.md"


def load_prepared():
    """Read what `speckit.ado.pr --prepare-only` wrote: (title, base, head, body_file)."""
    try:
        d = json.loads(Path(PREPARED_ARGS).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"cannot read {PREPARED_ARGS} ({e.__class__.__name__}); run `speckit.ado.pr --prepare-only` first")
    vals = [d.get(k) if isinstance(d, dict) else None for k in ("title", "base", "head")]
    if not all(isinstance(v, str) and v.strip() for v in vals):
        raise ValueError(f"{PREPARED_ARGS} must be a JSON object with non-empty string title, base and head")
    return (*vals, PREPARED_BODY)


_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def check_branch(kind, name):
    """Refuse refspec tricks (+x, :x, a:b), options, and odd git ref forms before any push/POST."""
    if (not _BRANCH.fullmatch(name) or ".." in name or "//" in name or "@{" in name
            or name.endswith(("/", ".", ".lock"))):
        raise ValueError(f"invalid {kind} branch name {name!r}: use letters, digits, '.', '_', '-', '/' only")


MAX_ID = 2147483647  # ADO work item ids are int32
_OUR_ID = re.compile(r"[aA][bB]([1-9][0-9]*)(?:-|$)")
_AB_REF = re.compile(r"(?<![A-Za-z0-9])AB#([1-9][0-9]*)(?![0-9])")


def story_from_branch(name, prefix):
    """The story a branch is tied to: `<prefix>ab<n>-…` / `<prefix>ab<n>` at the start, else `AB#<n>` anywhere.
    Spec Kit never emits `ab<digits>`, so its `001-x` / `20261028-143022-x` branches never count, whatever the prefix."""
    name = name[len("refs/heads/"):] if name.startswith("refs/heads/") else name
    m = (name.startswith(prefix) and _OUR_ID.match(name, len(prefix))) or _AB_REF.search(name)
    if not m or len(m.group(1)) > len(str(MAX_ID)) or int(m.group(1)) > MAX_ID:
        return None
    return int(m.group(1))


class _Refuse(Exception):
    """A reason `ado pr create` must refuse while review blocking is configured."""


# replace refs and old-style grafts rewrite history as git reports it: never let them hide a change
_GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}
# an inherited selector (a hook's GIT_INDEX_FILE, an outer GIT_DIR) must not point git at another repository or index
_GIT_SELECTORS = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                  "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_EXEC_PATH"}
GIT_TIMEOUT = 30  # seconds per git call


def git_env(base=None):
    """`base` (default: this process's environment) for a git run: no inherited repository selector or config injection,
    replace refs and grafts off, no pager, no prompt. Shared with strict._git."""
    env = {k: v for k, v in (os.environ if base is None else base).items()
           if k not in _GIT_SELECTORS and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}
    return {**env, **_GIT_ENV}


def _git(repo, *args):
    try:  # bytes: file names need not be UTF-8
        return subprocess.run(["git", "--no-pager", "--no-replace-objects", "-C", repo, *args], check=False, capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT, env=git_env())
    except FileNotFoundError:
        raise _Refuse("cannot check the review: `git` is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise _Refuse(f"cannot check the review: git did not answer within {GIT_TIMEOUT} s")
    except (OSError, subprocess.SubprocessError):
        raise _Refuse("cannot check the review: git could not be run")


def _text(r, what):
    """stdout of a git run, or _Refuse: failed runs and non-UTF-8 output never pass silently."""
    if r.returncode:
        err = (r.stderr.decode("utf-8", "replace").strip().splitlines() or ["git failed"])[-1]
        raise _Refuse(f"cannot check the review: {what} ({err})")
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise _Refuse(f"cannot check the review: {what} (git output is not UTF-8)")


def _repo_rel(repo, path):
    """cwd-relative `path` as a repo-root-relative posix path ('.' = root), or None if outside."""
    top = Path(_text(_git(repo, "rev-parse", "--show-toplevel"), "not a git repository").strip()).resolve()
    try:
        return Path(os.path.normpath(Path(repo).resolve() / path)).relative_to(top).as_posix()
    except ValueError:
        return None


def spec_dir_in_tree(spec_dir, dod_path, sha, repo="."):
    """The ledger's `Spec dir:` (untrusted) as a repo-root-relative path, or None unless it is
    relative, has no `..` part, is not `.`, is not dod_path or inside it, and commit `sha` has a
    regular-file (not symlink) spec.md or tasks.md directly in it."""
    if not spec_dir or PurePosixPath(spec_dir).is_absolute() or ".." in PurePosixPath(spec_dir).parts:
        return None
    rel, ledgers = _repo_rel(repo, spec_dir), _repo_rel(repo, dod_path)
    if rel in (None, ".") or (ledgers is not None and (rel + "/").startswith(ledgers + "/")):
        return None
    want = {f"{rel}/spec.md", f"{rel}/tasks.md"}
    out = _text(_git(repo, "ls-tree", "--full-tree", "-z", sha, "--", *sorted(want)), f"ls-tree {sha}")
    for entry in filter(None, out.split("\0")):  # "<mode> <type> <sha>\t<path>"; tree paths never follow symlinks
        meta, _, name = entry.partition("\t")
        if name in want and meta.split()[:2] in (["100644", "blob"], ["100755", "blob"]):
            return rel
    return None


def _section2_line(md):
    i = dod._section2_at(md)
    return -1 if i == -1 else len(md[:i].splitlines())


_REVIEW_HEADING = re.compile(r"^### Review\b.*$", re.M)
_HEADING_SHA = re.compile(r"@ `([^`]*)`")
_SHA_TEXT = re.compile(r"[0-9a-fA-F]{7,40}")
_BLOCKS_PR = re.compile(r"^- \*\*Blocks PR:\*\*\s*(yes|no)\b", re.M | re.I)
_SEVERITY = re.compile(r"^- \*\*Confirmed by severity:\*\*(.*)$", re.M)
_RERUN = " — re-run speckit.ado.review"


def review_blocks(work_item_id, block_on, head, dod_path, repo=".", push=False, squash=False, at_commit=False):
    """Why `ado pr create` must refuse under `review_block_on`, or None. Empty block_on → None
    (advisory). Blocking is opted into, so missing info (no review, no git, unknown head) refuses.
    The ledger is read from the head commit, never the working tree. `squash` (`ado strict check --phase done`): the
    reviewed commit is no ancestor of a squash-merged head, so only the verdict and counts are checked, not the staleness;
    a detached HEAD is read as the commit it is, without the origin comparison. `at_commit` (`ado strict check --head <sha>`, CI):
    `head` is a full commit id, read as that commit, without the origin comparison."""
    if not block_on:
        return None
    try:
        _check_review(work_item_id, block_on, head, dod_path, repo, push, squash, at_commit)
    except _Refuse as e:
        return str(e)
    return None


def _check_review(work_item_id, block_on, head, dod_path, repo, push, squash=False, at_commit=False):
    detached = False
    rev = None  # explicit refs/heads below: a tag named like the branch never shadows it
    if head == "HEAD":  # full ref, not --short: a tag named like the branch makes --short say heads/<name>
        sym = _git(repo, "symbolic-ref", "--quiet", "HEAD")
        detached = squash and sym.returncode == 1  # a CI checkout of the merged commit: there is no branch to compare
        if detached:
            rev = "HEAD^{commit}"
        else:
            ref = _text(sym, "HEAD is detached; pass a branch").strip()
            if not ref.startswith("refs/heads/"):
                raise _Refuse(f"cannot check the review: HEAD points at {ref!r}, not a branch")
            head = ref[len("refs/heads/"):]
    elif at_commit:
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise _Refuse(f"unknown head {head!r}")
        detached, rev = True, f"{head}^{{commit}}"
    head_sha = _text(_git(repo, "rev-parse", "--verify", rev or f"refs/heads/{head}^{{commit}}"), f"unknown head {head!r}").strip()
    if not push and not detached:  # without --push the PR opens from the remote branch, so it must be what we check
        r = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{head}^{{commit}}")
        if r.returncode == 0 and r.stdout.decode("ascii", "replace").strip() != head_sha:
            raise _Refuse(f"remote branch origin/{head} differs from the reviewed local branch; push or re-review")
    dod_rel = _repo_rel(repo, dod_path)
    ledger_rel = f"{dod_rel}/DoD_{work_item_id}.md" if dod_rel not in (None, ".") else f"DoD_{work_item_id}.md"
    r = _git(repo, "show", f"{head_sha}:{ledger_rel}") if dod_rel is not None else None
    if r is None or r.returncode:
        raise _Refuse(f"no review logged: {head} has no committed ledger {ledger_rel}" + _RERUN)
    ledger_md = _text(r, f"ledger {ledger_rel}")
    # blank fences/comments in the whole ledger first, so a fenced fake `## 4.` can't be the section found
    visible, still_open = _visible(ledger_md)
    # `ado dod` escapes story fences (F26), so an opener here is an older ledger or a hand edit: refused, not repaired.
    # Equal `## 2.` counts too: an unclosed opener plus a fenced example can shift fence parity and forge a §2.
    if (still_open or _section2_line(ledger_md) != _section2_line(visible)
            or len(dod._SECTION2_RE.findall(ledger_md)) != len(dod._SECTION2_RE.findall(visible))):
        raise _Refuse(f"cannot check the review: ledger contains an unclosed code fence or HTML comment (or one "
                      f"hiding a `## 2.` heading); re-pull §1 with `ado dod {work_item_id} --refresh` (it escapes "
                      "story text), or edit the ledger")
    log = dod.section(visible, 4) or ""
    live = [m for m in _REVIEW_HEADING.finditer(log) if not m.group(0).rstrip().endswith("(superseded)")]
    if not live:
        msg = "no review logged in ledger section 4"
        if any(not m.group(0).rstrip().endswith("(superseded)") for m in _REVIEW_HEADING.finditer(visible)):
            msg += " (a `### Review` block sits outside section 4, probably below `## 5.`: move it under `## 4.`)"
        raise _Refuse(msg + _RERUN)
    heading = live[-1]
    rest = log[heading.end():]
    nxt = re.search(r"^#{1,3} ", rest, re.M)
    block = rest[:nxt.start()] if nxt else rest
    verdicts = [v.lower() for v in _BLOCKS_PR.findall(block)]
    if not verdicts:
        raise _Refuse("latest review has no `Blocks PR:` line" + _RERUN)
    if "yes" in verdicts:
        raise _Refuse("latest review blocks the PR (`Blocks PR: yes`) — fix the findings and re-run speckit.ado.review")
    sev_lines = _SEVERITY.findall(block)
    if len(sev_lines) > 1:
        raise _Refuse("latest review has more than one `Confirmed by severity:` line (ambiguous)" + _RERUN)
    pairs = [(k.lower(), int(n)) for k, n in re.findall(r"([A-Za-z][\w-]*)\s+(\d+)", "".join(sev_lines))]
    counts = dict(pairs)
    if len(counts) != len(pairs):
        raise _Refuse("latest review repeats a severity on its `Confirmed by severity:` line (ambiguous)" + _RERUN)
    for s in block_on:  # the review's own verdict may predate this config, so recount
        if s.lower() not in counts:
            raise _Refuse(f"latest review does not report a `{s}` count on its `Confirmed by severity:` line" + _RERUN)
        if counts[s.lower()]:
            raise _Refuse(f"latest review has {counts[s.lower()]} confirmed `{s}` finding(s) and "
                          f"review_block_on includes `{s}` — fix them and re-run speckit.ado.review")
    if squash:
        return
    sha_m = _HEADING_SHA.search(heading.group(0))
    if not sha_m:
        raise _Refuse("latest review records no commit SHA" + _RERUN)
    sha = sha_m.group(1)
    if not _SHA_TEXT.fullmatch(sha):
        raise _Refuse(f"latest review SHA {sha!r} is not 7-40 hex characters" + _RERUN)
    # object lookup only: a branch or tag named like the short SHA must never stand in for it
    objs = _git(repo, "rev-parse", f"--disambiguate={sha.lower()}").stdout.decode("ascii", "replace").split()
    if len(objs) != 1 or _git(repo, "cat-file", "-t", objs[0]).stdout.strip() != b"commit":
        raise _Refuse(f"review SHA {sha} is ambiguous or not a commit" + _RERUN)
    full = objs[0]
    if _git(repo, "merge-base", "--is-ancestor", full, head_sha).returncode:
        raise _Refuse(f"review is stale: review is for {sha}, which is not an ancestor of {head}" + _RERUN)
    # Spec dir as the ledger had it at the reviewed commit: a later commit cannot widen what counts as spec
    old = _git(repo, "show", f"{full}:{ledger_rel}")
    old_spec = dod.ledger_spec_dir(_text(old, f"ledger {ledger_rel} at {sha}")) if old.returncode == 0 else None
    outside = _outside_ledger(repo, full, head_sha, dod_path, old_spec, work_item_id, whole_dirs=True)
    if outside:
        raise _Refuse(f"review is stale: review is for {sha}; later commits change {len(outside)} file(s) "
                      "outside the ledger/spec" + _RERUN)


def _outside_ledger(repo, base_sha, head_sha, dod_path, spec_dir, story_id, whole_dirs):
    """The files changed from commit `base_sha` to `head_sha` that are not the story's ledger material. `whole_dirs`
    (reviews): the ledger plus anything under `dod_path` or the valid spec dir. Else (verification records) exactly the
    ledger, `Decisions_<id>.md` and `<spec dir>/tasks.md`. The spec dir is the one valid at `base_sha`, or one that is new
    since (step 2a commits it after the review). Raises _Refuse when git cannot answer."""
    dod_rel = _repo_rel(repo, dod_path)
    if dod_rel is None:
        raise _Refuse("cannot check the review: dod_path is outside the repository")
    pre = f"{dod_rel}/" if dod_rel != "." else ""
    ledger_rel = f"{pre}DoD_{story_id}.md"
    # --no-renames: a move out of src/ into the ledger dir must still list the deleted path
    names = _text(_git(repo, "diff", "--name-only", "--no-renames", "-z", base_sha, head_sha, "--"),
                  f"git diff {base_sha[:12]} {head_sha[:12]}").split("\0")
    spec = spec_dir_in_tree(spec_dir, dod_path, base_sha, repo)
    if spec is None:  # step 2a commits the spec dir after the review: count it only if it is new since then
        new = spec_dir_in_tree(spec_dir, dod_path, head_sha, repo)
        if new and not _text(_git(repo, "ls-tree", "--full-tree", "-z", base_sha, "--", new), f"ls-tree {base_sha[:12]}"):
            spec = new
    if whole_dirs:
        prefixes = [p for p in (dod_rel, spec) if p not in (None, ".")]
        return [f for f in names if f and f != ledger_rel and not any(f.startswith(p + "/") for p in prefixes)]
    allowed = {ledger_rel, f"{pre}Decisions_{story_id}.md"} | ({f"{spec}/tasks.md"} if spec else set())
    return [f for f in names if f and f not in allowed]


def only_ledger_since(repo, base_sha, head, dod_path, spec_dir, story_id):
    """True when everything changed from commit `base_sha` to `head` is the story's ledger, its decisions file or
    the spec dir's tasks.md (the exact files, never a directory's worth). Raises _Refuse when git cannot answer."""
    return not _outside_ledger(repo, base_sha, head, dod_path, spec_dir, story_id, whole_dirs=False)


def push_branch(head):
    """`git push -u origin <head>`, list-form (no shell)."""
    try:
        r = subprocess.run(["git", "push", "-u", "origin", head], check=False, capture_output=True, text=True)
    except FileNotFoundError:
        raise ValueError("`git` is not installed or not on PATH")
    return {"error": r.stderr} if r.returncode else {}


def render_plan(host, title, body, base, head, work_item_ids, tasks_row=None):
    preview = " ".join(body.split())  # escaped once, with the whole value, by _cell below
    if len(preview) > 120:
        preview = preview[:120] + "..."
    rows = [("host", host), ("title", title), ("base", base), ("head", head),
            ("work items", ", ".join(f"AB#{i}" for i in work_item_ids)
             + (" (Azure: if the PR comes back without the story link, the link is added to the story)" if host == "azure" else "")),
            ("body", f"{len(body)} chars: {preview}")]
    if tasks_row:
        rows.insert(-1, tasks_row)  # (field, value), shown just above the body
    lines = ["Plan for pull request", "", "| field | value |", "|---|---|"]
    lines += [f"| {k} | {_cell(str(v))} |" for k, v in rows]
    # the gate shows this file, so it must carry the exact body that --apply sends
    return "\n".join(lines) + "\n\n## PR body (exactly as it will be sent)\n\n" + body
