"""Role sign-offs: the gates `signoff-tpo`, `signoff-qa` and `signoff-bpo` (they are not among the 12 workflow gates). A sign-off is an approver's
`/approve signoff-<role> <hash>` comment on the story, where the hash (`approvals.gate_hash`) covers the exact bytes signed and the signer also holds
the role in `roles.map` (the `not-role` rule of `approvals.guard`). Everything else is the comment approval of piece 4: the bypass-rules refusal, the
guards (`on-behalf`, `excluded-author`, `not-approver`, `edited`, `forged`), the newest command wins, an approval request newer than it makes it stale.

What each role signs: `tpo` every file of the ledger's Spec dir except the top-level `tasks.md` (ticking a task changes nothing), each framed by its
repo-relative path and byte length; `qa` and `bpo` the QA file `<dod_path>/QA_<id>.md`. Roles and whether a sign-off is required are trust keys: only
the TARGET branch's `roles` counts (`config.trusted`). This is process enforcement, not a security boundary against anyone holding approver, bypass or
admin rights. Every refusal here is a fixed text; nothing from a comment is ever shown."""
import os
import pathlib
import re
import subprocess

from . import approvals, comments, dod, gate, qa, rules
from .config import SIGNOFF_ROLES

ROLES = tuple(sorted(SIGNOFF_ROLES, key=("tpo", "qa", "bpo").index))  # the order they are done in
GATES = tuple(f"signoff-{r}" for r in ROLES)
PREFIX = "signoff-"
MAX_FILES = 200
MAX_TOTAL = 8 << 20
E_ARTIFACT = ("sign-off: what this gate covers cannot be read (the ledger or its Spec dir, or the QA file, is missing, a link, outside the working tree, over "
              "1 MiB, not UTF-8 or not in the form needed; or the Spec dir holds more than 200 files, an odd file name or more than 8 MiB), so there is nothing to sign")
E_TERMINAL = "a required sign-off is checked from the story's comments, so `approval` must be \"comment\" on the target branch; it is not"
E_TRUST = ("--base and the default branch (origin/HEAD) differ in `roles`, `approvers` or `approval`, so it is unclear who may sign off; "
           "a human closes the story: `ado done <id>` without --base")
ONE_PERSON = "signoff-one-person: one identity gave more than one sign-off (allowed; shown so the team knows)"
E_OFFLINE = "not verified online: this is only the line a signer comments; run the check without --offline"
E_OFFLINE_GATE = "--offline applies to the sign-off gates only (signoff-tpo, signoff-qa, signoff-bpo)"
NOT_REQUIRED = "not required: `roles.signoff` on the target branch does not list this role (or `roles` is absent)"
E_SIGNOFF = ("signoff-{role} is not met on story {story} [{rule}]: {why}. A trusted approver who holds the {role} role must comment `{line}` "
             "(first line, unedited, newer than any approval request).{bad}")
WHY = {
    "missing": "no sign-off comment counts yet",
    "stale": "the newest sign-off names other bytes than the ones now on disk, or an approval request is newer than it",
    "rejected": "the newest command on this gate is a /reject",
    "not-role": "an approver commented, but does not hold this role in `roles.map`",
    "not-approver": "a comment was made by someone who is not in `approvers`",
    "excluded-author": "a comment is by the token identity or a kit posting identity, which can never sign",
    "edited": "a sign-off comment was edited",
    "on-behalf": "a sign-off comment was written on someone's behalf (what a rules-bypass write leaves)",
    "forged": "a sign-off comment's author is not the identity that authenticated the write",
    "malformed-newer": "a newer malformed command line by an approver is on the thread",
}
_SKIPPED = ("on-behalf", "forged", "edited", "excluded-author", "not-role", "not-approver")  # the order they are named in, closest to a signature first


class SignoffRefused(approvals.ApprovalRefused):
    """A refusal that is not 'no signature yet'. `rule` is its id: signoff-bypass, signoff-unreadable or signoff-terminal."""

    def __init__(self, rule, text):
        super().__init__(f"{rule}: {text}")
        self.rule = rule


class SignoffMissing(approvals.ApprovalMissing):
    """The sign-off is not met. `rule`: missing, stale, rejected, malformed-newer, or the guard rule that kept a comment out (see `WHY`)."""

    def __init__(self, rule, text):
        super().__init__(text)
        self.rule = rule


def role_of(gate_id):
    """The role of a sign-off gate id, or None when it is not one of the three."""
    return gate_id[len(PREFIX):] if gate_id in GATES else None


def required(trust, role):
    return trust.roles is not None and role in trust.roles["signoff"]


def holders(trust, role):
    return {who for who, roles in trust.roles["map"].items() if role in roles}


def not_required(gate_id):
    return {"gate": gate_id, "met": True, "required": False, "note": NOT_REQUIRED}


# ---- what is signed ------------------------------------------------------------------------------------------------------------------------------

def _frame(label, raw):
    return f"{label} {len(raw)}\n".encode("utf-8") + raw + b"\n"


def _spec_dir(story_id, cfg, repo):
    """The ledger's `Spec dir` as a clean repo-relative POSIX path (no `..`, no empty or `.` part, no backslash, not absolute), else ValueError."""
    ledger = gate._read(os.path.join(repo, dod.dod_file(cfg.dod_path, comments._num(story_id))), repo, set())
    spec_dir = dod.ledger_spec_dir(ledger) if ledger is not None and not dod.unclosed(ledger) else None
    parts = pathlib.PurePosixPath(spec_dir).parts if spec_dir else ()
    if not parts or "\\" in spec_dir or "\x00" in spec_dir or spec_dir != "/".join(parts) or any(p in ("", ".", "..") for p in parts) or parts[0] == "/":
        raise ValueError(E_ARTIFACT)
    if any(os.path.islink(os.path.join(repo, *parts[:i + 1])) for i in range(len(parts))):  # scandir would follow a link and list a folder elsewhere
        raise ValueError(E_ARTIFACT)
    return "/".join(parts)


def _walk(repo, rel, out, budget):
    """Collect `(repo-relative path, text)` of every file under `rel`: a link, a hard link (it may name a file outside the repo), a special file, a name
    with a control character, a file that `gate._read` refuses (over 1 MiB, not UTF-8, outside the tree), too many files or more than `budget[0]` bytes
    in all is ValueError(E_ARTIFACT)."""
    try:
        with os.scandir(os.path.join(repo, rel)) as it:
            entries = sorted(it, key=lambda e: e.name.encode("utf-8"))
    except (OSError, UnicodeEncodeError):
        raise ValueError(E_ARTIFACT) from None
    for e in entries:
        path = f"{rel}/{e.name}"
        if e.is_symlink() or any(ord(c) < 32 or ord(c) == 127 for c in e.name):
            raise ValueError(E_ARTIFACT)
        if e.is_dir(follow_symlinks=False):
            _walk(repo, path, out, budget)
        elif e.is_file(follow_symlinks=False):
            st = e.stat(follow_symlinks=False)
            budget[0] -= st.st_size
            text = gate._read(os.path.join(repo, path), repo, set()) if st.st_nlink == 1 and budget[0] >= 0 else None
            if text is None or len(out) >= MAX_FILES:
                raise ValueError(E_ARTIFACT)
            out.append((path, text))
        else:
            raise ValueError(E_ARTIFACT)


def artifact(role, story_id, cfg, repo="."):
    """The bytes a role signs (for `approvals.gate_hash`); ValueError(E_ARTIFACT) for anything missing or unreadable."""
    if role == "tpo":
        spec_dir, files = _spec_dir(story_id, cfg, repo), []
        _walk(repo, spec_dir, files, [MAX_TOTAL])
        parts = [(p, t.encode("utf-8")) for p, t in sorted(files, key=lambda f: f[0].encode("utf-8")) if p != f"{spec_dir}/tasks.md"]
        if not parts:  # an empty Spec dir (or one holding only tasks.md) has nothing to sign
            raise ValueError(E_ARTIFACT)
        return b"".join(_frame(p, b) for p, b in parts)
    if role in ("qa", "bpo"):
        text = rules.read_file(os.path.join(repo, qa.qa_file(cfg.dod_path, comments._num(story_id))), repo, os.path.join(repo, cfg.dod_path))
        if text is None:
            raise ValueError(E_ARTIFACT)
        return _frame("qa", text.encode("utf-8"))
    raise ValueError(approvals.E_GATE)


def _hash(role, story_id, cfg, repo):
    return approvals.gate_hash(PREFIX + role, story_id, artifact(role, story_id, cfg, repo))


# ---- the check ----------------------------------------------------------------------------------------------------------------------------------

def offline(trust, story_id, role, cfg, repo="."):
    """What a signer must comment, from the working tree alone (no token, no request). Never a pass: the result says `met` False, `verified` False."""
    if not required(trust, role):
        return not_required(PREFIX + role)
    h = _hash(role, story_id, cfg, repo)
    return {"gate": PREFIX + role, "met": False, "verified": False, "hash": h, "line": f"/approve {PREFIX}{role} {h}", "note": E_OFFLINE}


def check(client, trust, story_id, role, cfg, repo=".", ci_identity=None):
    """`{"gate", "mode", "met": True, "required": True, "hash", "comment_id", "author"}` when the sign-off is met, `{"met": True, "required": False, ...}`
    when the target branch does not require it, else SignoffRefused (bypass, unreadable thread, terminal mode) or SignoffMissing (every other reason), fixed
    text. Reads only. Order: not required, the mode, the bypass-rules refusal, the artifact, the thread."""
    gate_id = PREFIX + role
    if not required(trust, role):
        return not_required(gate_id)
    if trust.approval != "comment":
        raise SignoffRefused("signoff-terminal", E_TERMINAL)
    try:
        approvals.comment_mode_allowed(client)
    except approvals.ApprovalRefused:
        raise SignoffRefused("signoff-bypass", approvals.BYPASS_REFUSAL) from None
    h = _hash(role, story_id, cfg, repo)
    try:
        found, skip = approvals._load(client, trust, story_id, ci_identity)
    except approvals.ApprovalMissing as e:  # (no approvers cannot happen: `roles.signoff` needs an approver who holds the role)
        raise SignoffRefused("signoff-unreadable", str(e)) from None
    found, tally = approvals.cached(found), {}
    win = approvals.newest(client, story_id, found, gate_id, trust.approvers, skip, tally=tally, holders=holders(trust, role))
    if win is None:
        rule = next((r for r in _SKIPPED if tally.get(r)), "missing")
    elif win["verb"] == "reject":
        rule = "rejected"
    elif win["hash"] != h:
        rule = "stale"
    else:
        cut, reqs = approvals._order({"date": win["created"], "id": win["id"]}), approvals._requests(found, {gate_id: h})
        rule = "stale" if None in reqs or any(r >= cut for r in reqs) else "malformed-newer" if win["bad_newer"] else None
    if rule is not None:
        raise SignoffMissing(rule, E_SIGNOFF.format(role=role, story=int(story_id), rule=rule, why=WHY[rule], line=f"/approve {gate_id} {h}",
                                                    bad=approvals._bad(win["bad_newer"]) if win and win["bad_newer"] else ""))
    return {"gate": gate_id, "mode": "comment", "met": True, "required": True, "hash": h,
            "comment_id": int(win["id"]) if str(win["id"]).isdigit() else None, "author": win["author"]}  # ADO's id is printed only when it is a number


# ---- what `done` records ------------------------------------------------------------------------------------------------------------------------

def head_sha(repo="."):
    """The commit the working tree is at, `unknown` when there is none; with uncommitted changes the hash covers bytes that commit lacks, and it says so."""
    try:
        out = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if not re.fullmatch(r"[0-9a-f]{40,64}", out):
        return "unknown"
    return f"{out} (uncommitted changes: the bytes signed are not exactly this commit)" if dirty else out


def _cid(result):
    return "comment " + (str(result["comment_id"]) if result["comment_id"] is not None else "(id not shown)")


def plan_lines(met, sha):
    """The lines `done`'s plan adds for the sign-offs it relied on (`met`: `[(role, check result)]`); nothing when none were required."""
    return "".join([f"\nSign-offs met on the bytes now on disk:\n"] + [f"signoff {r}: {_cid(v)}\n" for r, v in met] + [f"bytes: {sha}\n"]) if met else ""


def history_note(met, sha):
    """The one line recorded on the story with the state change, or None."""
    return "Sign-offs: " + "; ".join(f"{r} {_cid(v)}" for r, v in met) + f"; bytes {sha}" if met else None
