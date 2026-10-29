"""`ado branch`: tie the local feature branch to the story as `<branch_prefix>ab<id>-<slug>` (local git only, never pushes);
`--link` (Azure Repos) adds it to the story's Development section."""
import json
import os
import re
import subprocess
import unicodedata
import urllib.parse
from pathlib import Path

from . import prs, workitems
from .push import _cell

SLUG_MAX = 40
# a Spec Kit feature segment: sequential `001-x` or timestamp `20260319-143022-x`
_SPECKIT_SEGMENT = re.compile(r"[0-9]{3,}-|[0-9]{8}-[0-9]{6}-")


def slug(title):
    """ASCII-folded, lowercase `[a-z0-9-]`, dashes collapsed, at most 40 chars, cut at a word boundary when it has one; never empty."""
    folded = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()
    full = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    cut = full[:SLUG_MAX]
    if len(full) > SLUG_MAX and full[SLUG_MAX] != "-" and "-" in cut:  # mid-word: drop the partial word
        cut = cut.rsplit("-", 1)[0]
    return cut.rstrip("-") or "story"


def _git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise ValueError("`git` is not installed or not on PATH")


def _out(*args):
    r = _git(*args)
    return None if r.returncode else r.stdout.strip()


def require_work_tree():
    """Before any ADO request: `ado branch` only works inside a git work tree."""
    if _out("rev-parse", "--is-inside-work-tree") != "true":
        raise ValueError("not a git repository (or not inside its work tree)")


def _default_branches(heads):
    """The default branch's possible names: origin/HEAD, init.defaultBranch, a local main/master. May be empty."""
    names = {_out("config", "init.defaultBranch")} | {h for h in heads if h in ("main", "master")}
    origin_head = _out("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if origin_head:
        names.add(origin_head.removeprefix("refs/remotes/origin/"))
    return names - {None, ""}


def _renamable(current, heads):
    """Rename only a Spec Kit branch made for this spec, with no upstream, when the default branch is known
    and it is not that branch. Anything else gets a new branch instead."""
    defaults = _default_branches(heads)
    return (bool(defaults) and current not in defaults and _made_for_current_spec(current)
            and not _out("for-each-ref", "--format=%(upstream)", f"refs/heads/{current}"))


def _made_for_current_spec(current):
    """A Spec Kit branch whose feature segment is the spec dir `.specify/feature.json` points at (this spec)."""
    segment = current.rsplit("/", 1)[-1]
    if not _SPECKIT_SEGMENT.match(segment):
        return False
    try:
        data = json.loads((Path(_out("rev-parse", "--show-toplevel")) / ".specify" / "feature.json")
                          .read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return False
    spec_dir = data.get("feature_directory") if isinstance(data, dict) else None
    return isinstance(spec_dir, str) and Path(spec_dir).name == segment


def plan(story_id, title, prefix):
    """{"branch", "action": exists|switch|rename|create[, "from"]} for tying HEAD to the story."""
    target = f"{prefix}ab{story_id}-{slug(title)}"
    prs.check_branch("story", target)
    current = _out("symbolic-ref", "--quiet", "--short", "HEAD")  # None when detached
    if current and prs.story_from_branch(current, prefix) == story_id:
        return {"branch": current, "action": "exists"}
    heads = [r.removeprefix("refs/heads/")
             for r in (_out("for-each-ref", "--format=%(refname)", "refs/heads") or "").splitlines()]
    mine = [h for h in heads if prs.story_from_branch(h, prefix) == story_id]
    if mine:
        return {"branch": target if target in mine else mine[0], "action": "switch"}
    if current and _renamable(current, heads):
        return {"branch": target, "action": "rename", "from": current}
    return {"branch": target, "action": "create"}


def apply(p):
    cmd = {"switch": ["switch", p["branch"]], "create": ["switch", "-c", p["branch"]],
           "rename": ["branch", "-m", p.get("from"), p["branch"]]}.get(p["action"])
    if cmd:
        r = _git(*cmd)
        if r.returncode:
            err = (r.stderr.strip().splitlines() or ["git failed"])[-1]
            raise ValueError(f"git {' '.join(cmd)} failed: {err}")
    return {**p, "applied": True}


def artifact_url(project_id, repo_id, branch):
    """The Development-section link to a branch: ids as ADO returns them, the branch percent-encoded (`/` too)."""
    return f"vstfs:///Git/Ref/{project_id}%2F{repo_id}%2FGB{urllib.parse.quote(branch, safe='')}"


def link_value(url):
    return {"rel": "ArtifactLink", "url": url, "attributes": {"name": "Branch"}}


_REF_URL = re.compile(r"vstfs:///Git/Ref/([^%/]+)%2[fF]([^%/]+)%2[fF]GB(.*)")


def _key(url):
    """Compare ArtifactLinks regardless of GUID case and %-escape case; the branch name itself stays case-sensitive."""
    m = _REF_URL.fullmatch(url)
    return (m[1].lower(), m[2].lower(), re.sub(r"%[0-9a-f]{2}", lambda h: h[0].upper(), m[3])) if m else url


def _guid(res, what):
    if not isinstance(res, dict) or not res.get("id"):
        raise ValueError(f"cannot read the {what} id from ADO")
    return res["id"]


_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]*@")


def _ls_remote(ref):
    """`git ls-remote --heads origin <ref>`: never prompts, gives up after 30 s; a failure names the first
    `fatal:` line with any url userinfo removed."""
    try:
        r = subprocess.run(["git", "ls-remote", "--heads", "origin", ref], capture_output=True, text=True,
                           check=False, timeout=30, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except FileNotFoundError:
        raise ValueError("`git` is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise ValueError("cannot read remote `origin`: timed out after 30 s")
    if r.returncode:
        lines = r.stderr.strip().splitlines()
        fatal = next((l for l in lines if l.startswith("fatal:")), lines[0] if lines else "git failed")
        raise ValueError("cannot read remote `origin`: " + _USERINFO.sub(r"\1", fatal))
    return r


def link_plan(client, story_id):
    """{"id", "branch", "repository", "url", "action": link|exists} for adding the current branch to the story.
    Local and git checks first, then read-only GETs; nothing is written."""
    cfg = client.config
    if cfg.repo_host != "azure":
        raise ValueError("`ado branch --link` is Azure Repos only; on GitHub the Azure Boards GitHub app links "
                         "the branch and PR through `AB#<id>` references instead")
    if not cfg.repository:
        raise ValueError("repo_host is azure but `repository` is not set in ado.config.json")
    current = _out("symbolic-ref", "--quiet", "--short", "HEAD")
    if not current:
        raise ValueError("HEAD is detached; switch to the story's branch first")
    if prs.story_from_branch(current, cfg.branch_prefix) != story_id:
        raise ValueError(f"current branch {current!r} is not tied to story {story_id}; "
                         f"run `ado branch {story_id} --apply` first")
    ref = f"refs/heads/{current}"
    r = _ls_remote(ref)
    if ref not in [line.split("\t")[-1] for line in r.stdout.splitlines()]:
        raise ValueError(f"branch {current!r} is not on the remote: push the branch first "
                         f"(git push -u origin {current})")
    api = f"api-version={cfg.api_version}"
    project = urllib.parse.quote(cfg.project)
    url = artifact_url(
        _guid(client.get(f"/_apis/projects/{project}?{api}"), "project"),
        _guid(client.get(f"/{project}/_apis/git/repositories/{urllib.parse.quote(cfg.repository, safe='')}?{api}"),
              "repository"), current)
    linked = any(r.get("rel") == "ArtifactLink" and _key(r.get("url") or "") == _key(url)
                 for r in workitems.get(client, story_id).get("relations") or [])
    return {"id": story_id, "branch": current, "repository": cfg.repository, "url": url,
            "action": "exists" if linked else "link"}


def render_link_plan(p):
    head = f"Plan for linking a branch to work item {p['id']}"
    if p["action"] == "exists":
        return f"{head}\n\nAlready linked; nothing to do.\n"
    rows = [("branch", p["branch"]), ("repository", p["repository"]), ("link", p["url"])]
    return (f"{head}\n\n| field | value |\n|---|---|\n" + "\n".join(f"| {k} | {_cell(v)} |" for k, v in rows)
            + "\n\nOn --apply: add this ArtifactLink (name \"Branch\") to the story's Development section.\n")
