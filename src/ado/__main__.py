import argparse
import json
import os
import sys
from datetime import date

from pathlib import Path

from . import __version__, attachments, branch, comments, decisions, dod, done, gate, prs, push, questions, stack, strict, tasks, workitems
from .auth import AuthError
from .client import AdoClient, AdoError
from .config import Config, ConfigError, ConfigInvalid
from .setup_cmd import run_setup


def _fields(pairs):
    bad = next((p for p in pairs if "=" not in p), None)
    if bad is not None:
        raise ValueError(f"--field expects Name=Value: {bad}")
    fields = dict(pair.partition("=")[::2] for pair in pairs)
    if any(name.lower() == "system.parent" for name in fields):
        raise ValueError("--field System.Parent is not applied by ADO; use: ado link <id> --parent <n>")
    return fields


def _tasks_path(client, args):
    if args.source:
        path = Path(args.source)
    else:
        ledger = dod.dod_file(client.config.dod_path, args.id)
        try:
            spec_dir = dod.ledger_spec_dir(ledger.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"no --from given and cannot read ledger {ledger} ({e.__class__.__name__}); "
                             f"pass --from PATH or run `ado dod {args.id} --spec-dir DIR`")
        if not spec_dir:
            raise ValueError(f"no --from given and {ledger} has no spec dir; "
                             f"pass --from PATH or run `ado dod {args.id} --refresh --spec-dir DIR`")
        path = Path(spec_dir) / "tasks.md"
    if not path.is_file():
        raise ValueError(f"tasks file not found: {path}")
    return path


def _plan(args, text):
    """--expect-plan: refuse an --apply whose plan is not the approved file (writes the fresh one to
    <file>.new). --plan-out: write the preview and drop a stale <file>.new. Runs before any write."""
    if args.expect_plan:
        approved = Path(args.expect_plan)
        try:  # text mode on both sides, so the newline translation of --plan-out on Windows still matches
            same = approved.read_text(encoding="utf-8") == push.gate_preview(text, args.expect_plan)
        except (OSError, UnicodeDecodeError):
            same = False
        if not same:
            fresh = Path(f"{approved}.new")
            fresh.parent.mkdir(parents=True, exist_ok=True)
            fresh.write_text(push.gate_preview(text, fresh), encoding="utf-8")
            raise ValueError(f"plan changed since approval — re-run the preview and approve again "
                             f"(the current plan is in {fresh})")
    if args.plan_out:
        out = Path(args.plan_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(push.gate_preview(text, args.plan_out), encoding="utf-8")
        Path(f"{out}.new").unlink(missing_ok=True)


def _push(client, args):
    path = _tasks_path(client, args)
    try:
        parsed = tasks.parse_tasks(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read {path}: {e}")
    ops = push.plan_push(client, args.id, parsed)  # read-only: GET/WIQL only
    _plan(args, push.render_plan(args.id, ops))
    result = push.apply_push(client, args.id, ops) if args.apply else {}
    return {"parent": args.id, "from": str(path), "applied": args.apply, "ops": ops, **result}


def _stamp_used(client, args, ids):
    """The apply paths, after the strict check passed and just before anything is sent: stamp `used` on the overrides it relied on.
    A failed write is exit 2 with nothing sent."""
    if ids:
        decisions.apply_stamp(decisions.decisions_file(client.config.dod_path, args.id), args.id, ids)


def _done(client, args):
    used = strict.require(strict.check(client, args.id, "done"))["overrides_used"] if client.config.mode == "strict" else []
    path = dod.dod_file(client.config.dod_path, args.id)
    # both files are uploaded: read them as confined as the gate does (no symlink, real path under the working tree, 1 MiB)
    ledger_md = gate._read(path, ".", set()) if os.path.lexists(path) else None
    if ledger_md is None:
        raise ValueError(f"cannot read ledger {path} (missing, a symlink, outside the working tree, too large or not UTF-8); "
                         f"run `ado dod {args.id}` first")
    dec = decisions.decisions_file(client.config.dod_path, args.id)
    if os.path.lexists(dec) and gate._read(dec, ".", set()) is None:
        raise ValueError("the decisions file is not a regular file inside the working tree "
                         "(a symlink, outside it, a directory, over 1 MiB or not UTF-8); refusing to attach it")
    chk = done.check(ledger_md)
    work_item = None
    if chk["ready"] or not args.apply:  # one read-only GET drives the preview, the idempotent apply and `already_done` (CI must not move a done story)
        try:
            work_item = workitems.get(client, args.id)
        except (AdoError, AuthError):
            if args.apply:
                raise  # never write blind
    known = {"already_done": done._status(work_item, "", client.config.done_state)[0]} if work_item else {}
    dec = dec if os.path.lexists(dec) else None  # attached next to the ledger only when it exists (checked above)
    _plan(args, done.render_plan(args.id, chk, client.config, work_item, dec))
    if args.apply and chk["ready"]:
        _stamp_used(client, args, used)
    result = done.apply(client, args.id, path, work_item, dec) if args.apply and chk["ready"] else {}
    return {"id": args.id, "applied": bool(args.apply and chk["ready"]), **chk, **known,
            **({"decisions_file": str(dec)} if dec else {}), **result}


def _read_body(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read body file {path} ({e.__class__.__name__})")


def _task_status(client, args):
    """(unticked ids or None, preview row). Missing tasks.md / spec dir never blocks: it is only noted."""
    try:
        path = _tasks_path(client, argparse.Namespace(source=None, id=args.id))
        parsed = tasks.parse_tasks(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeDecodeError):
        return None, ("tasks", "not checked (no tasks.md)")
    if not parsed:
        # An emptied tasks.md (R3 N3) must not read as "nothing to check" while the board still tracks Tasks for it.
        try:
            tracked = [m.group(1) for c in workitems.children(client, args.id)
                       if c.get("state") != client.config.removed_state and (m := push._TAG_RE.match(c.get("title") or ""))]
        except (AdoError, ValueError, KeyError, TypeError):
            tracked = []
        if tracked:
            return tracked, ("draft", f"yes — {path} lists no tasks but {len(tracked)} Task(s) tracked on the board "
                                      f"({', '.join(tracked)}); was it emptied?")
        return None, ("tasks", f"not checked (no tasks found in {path})")
    open_ids = [t["id"] for t in parsed if not t["done"]]
    if not open_ids:
        return [], ("tasks", f"all {len(parsed)} ticked")
    return open_ids, ("draft", f"yes — {len(open_ids)} unticked task(s): {', '.join(open_ids)}")


def _pr(client, args):
    cfg = client.config
    azure = cfg.repo_host == "azure"
    if args.pr_command == "comment":
        if not azure:
            raise ValueError("`ado pr comment` is Azure Repos only; on GitHub use `gh pr comment`")
        if not cfg.repository:
            raise ValueError("repo_host is azure but `repository` is not set in ado.config.json")
        return prs.azure_comment(client, cfg.repository, args.pr_id, _read_body(args.body_file))
    if azure and not cfg.repository:
        raise ValueError("repo_host is azure but `repository` is not set in ado.config.json")
    if args.from_prepared:
        if any((args.title, args.body_file, args.base, args.head)):
            raise ValueError("--from-prepared cannot be combined with --title/--body-file/--base/--head")
        args.title, args.base, args.head, args.body_file = prs.load_prepared()
    else:
        if args.push:
            raise ValueError("--push needs --from-prepared")
        if not all((args.title, args.body_file, args.base, args.head)):
            raise ValueError("--title, --body-file, --base and --head are required (or use --from-prepared)")
    prs.check_branch("base", args.base)
    prs.check_branch("head", args.head)
    used = []
    if cfg.mode == "strict":  # first: any finding refuses before a plan file, a push or a request
        if not strict.head_is_checkout(".", args.head):
            raise ValueError("strict mode judges the working-tree ledger and decisions file: check out the --head branch first")
        used = strict.require(strict.check(client, args.id, "pr", head=args.head, base=strict.base_ref(".", args.base),
                                           push=args.push))["overrides_used"]
    if why := prs.review_blocks(args.id, cfg.effective_review_block_on(), args.head, cfg.dod_path, push=args.push):
        raise ValueError(f"refusing to open a PR: {why}")
    open_ids, tasks_row = _task_status(client, args)
    if open_ids and not args.allow_incomplete:
        raise ValueError(f"refusing to open a PR: {len(open_ids)} unticked task(s) in tasks.md: {', '.join(open_ids)}. "
                         "Finish them, or pass --allow-incomplete to open it as a draft")
    draft = bool(open_ids)
    body = _read_body(args.body_file)
    title = prs.with_ab_ref(args.title, args.id)
    host = "azure" if azure else "github"
    _plan(args, prs.render_plan(host, title, prs.azure_description(body) if azure else body, args.base, args.head,
                                [args.id], tasks_row))
    info = {"applied": args.apply, "host": host, "title": title, "base": args.base, "head": args.head}
    if not args.apply:
        return info
    _stamp_used(client, args, used)
    if args.push and (pushed := prs.push_branch(args.head)).get("error"):
        return pushed
    if azure:
        res = prs.azure_create(client, cfg.repository, args.head, args.base, title, body, [args.id], draft)
        if res.get("pullRequestId"):
            res["work_items_linked"], note = prs.ensure_work_item_links(client, cfg.repository, res, [args.id])
            if note:
                print(f"note: {note}", file=sys.stderr)
    else:
        res = prs.github_create(title, args.body_file, args.base, args.head, draft)
    return res if res.get("error") else {**info, **res}


def dispatch(client, args):
    if args.command == "strict":
        if args.strict_command == "verify":
            return strict.verify(client, args.id, record=args.record, base=args.base, allow_override=args.allow_override)
        base = args.base
        if base is None and args.phase == "pr" and client.config.mode == "strict":
            base = strict.default_base(".")  # "" when origin/HEAD is not set: the finding base-unknown, never a pass
        return strict.check(client, args.id, args.phase, head=args.head, base=base)
    if args.command == "pr":
        return _pr(client, args)
    if args.command == "done":
        return _done(client, args)
    if args.command == "push":
        return _push(client, args)
    if args.command == "branch":
        branch.require_work_tree()  # before the ADO request
        if args.link:
            p = branch.link_plan(client, args.id)  # read-only
            _plan(args, branch.render_link_plan(p))
            do = args.apply and p["action"] == "link"
            if do:
                workitems.add_relation(client, args.id, branch.link_value(p["url"]))
            return {**p, "applied": do}
        title = (workitems.get(client, args.id).get("fields") or {}).get("System.Title", "")
        p = branch.plan(args.id, title, client.config.branch_prefix)
        return branch.apply(p) if args.apply else {**p, "applied": False}
    if args.command == "get":
        return workitems.get(client, args.id)
    if args.command == "query":
        return workitems.query(client, args.wiql)
    if args.command == "create":
        return workitems.create(client, args.type, args.title, _fields(args.field))
    if args.command == "update":
        fields = _fields(args.field)
        done = client.config.done_state  # a non-empty string: the config validates it
        sent = {str(args.state or "").strip().casefold(), *(str(v).strip().casefold() for k, v in fields.items()
                                                           if k.strip().lower() == "system.state")}
        if client.config.mode == "strict" and isinstance(done, str) and done.strip() and done.strip().casefold() in sent:
            raise ValueError("strict mode: close the story with `ado done <id>` (it runs the strict done check); "
                             "`ado update` cannot move it to the done state")
        return workitems.update(client, args.id, args.state, fields)
    if args.command == "comments":
        out = comments.safe_out(args.out or f".ado/context/{args.id}.md")  # before any request
        found = comments.fetch(client, args.id)
        comments.write(args.id, found, out)
        return found
    if args.command == "attachments":
        out_dir = attachments.safe_dir(args.dir or f".ado/attachments/{args.id}") if args.download else None
        return attachments.fetch(client, args.id, out_dir)  # out_dir is checked before any request
    if args.command == "comment":
        return workitems.comment(client, args.id, args.text)
    if args.command == "link":
        return workitems.link(client, args.id, args.to, args.url, args.parent)
    if args.command == "attach":
        return attachments.attach(client, args.id, args.files, args.comment)
    if args.command == "dod":
        wi = workitems.get(client, args.id)
        dod_dir = args.dir or client.config.dod_path
        existed = dod.dod_file(dod_dir, args.id).exists()
        path = dod.write_dod(
            wi, dod_dir, client.config.organization, client.config.project,
            synced=date.today().isoformat(), force=args.force, refresh=args.refresh,
            spec_dir=getattr(args, "spec_dir", None) or "",
        )
        action = "refreshed" if (args.refresh and existed) else ("overwritten" if existed else "created")
        return {"path": str(path), "action": action, **dod.ac_stats(wi)}
    if args.command == "questions":
        path = dod.dod_file(client.config.dod_path, args.id)
        try:
            parsed = questions.parse_questions(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"cannot read ledger {path} ({e.__class__.__name__}); run `ado dod {args.id}` first")
        if not (args.post or args.plan_out):
            return parsed
        plan = questions.plan_post(client, args.id, parsed)  # read-only: GET only
        _plan(args, questions.render_plan(args.id, plan, parsed["unparsed"]))
        if args.apply:
            questions.apply_post(client, args.id, plan)
        return {"id": args.id, "applied": args.apply, "action": plan["action"], "comment_id": plan["comment_id"]}
    if args.command == "decisions":  # read-only unless --post/--plan-out; only --apply writes (comment, then the file)
        path = _decisions_path(client.config.dod_path, args.id, args.if_any)  # None only with --if-any and no file
        try:
            parsed = decisions.parse_decisions(path.read_text(encoding="utf-8") if path else "")
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"cannot read the decisions file {path} ({e.__class__.__name__})")
        if not (args.post or args.plan_out):
            return parsed
        plan = decisions.plan_post(client, args.id, parsed)
        _plan(args, decisions.render_plan(args.id, plan, parsed["unparsed"]))
        res = decisions.apply_post(client, args.id, plan, path) if args.apply else {}
        return {"id": args.id, "applied": args.apply, "action": plan["action"], "lines": plan["lines"],
                "comment_id": res.get("comment_id"), **({"note": "no decisions file; nothing to post"} if path is None else {})}
    if args.command == "gate":
        path = dod.dod_file(client.config.dod_path, args.id)
        if not path.exists():
            return {"ledger": "missing", "blocking": False,
                    "note": f"no ledger at {path} — run `ado dod {args.id}` to enable tracking"}
        # dod_path comes from the PR's config: read the ledger as confined as any file under --src
        ledger_md = gate._read(path, args.src, set())
        if ledger_md is None:
            return {"ledger": "unreadable", "blocking": False,
                    "note": f"ledger at {path} exists but is unreadable — advisory only"}
        dpath = decisions.decisions_file(client.config.dod_path, args.id)
        decisions_md = gate._read(dpath, args.src, set()) if os.path.lexists(dpath) else None  # confined like the ledger
        unreadable = os.path.lexists(dpath) and decisions_md is None
        tests = {**gate.DEFAULT_TESTS, **client.config.gate["tests"]}
        work_item, reason = None, gate.UNREACHABLE
        if getattr(args, "offline", False):
            reason = "offline — drift not checked"
        else:
            try:
                work_item = workitems.get(client, args.id)
            except (AdoError, AuthError):
                pass  # missing info (no story, no token) never blocks: drift is simply not checked
        skipped = set()
        sources = gate.discover_tests(args.src, tests, skipped)
        changed = {f: t for f in (args.changed or []) if (t := gate._read(f, args.src, skipped)) is not None}
        return gate.run_gate(ledger_md, sources, changed, work_item, skipped=len(skipped),
                             block=client.config.effective_gate_block(), tests=tests, reason=reason,
                             commits=gate.check_commits(args.src, args.id, _gate_base(client.config, args)),
                             decisions_md=decisions_md, decisions_unreadable=unreadable)
    raise SystemExit(2)


def _gate_base(cfg, args):
    """The ref `check_commits` walks from: the `--base` as given, but in strict mode the full commit id it resolves to (a tag named like
    the base must not move the range the strict floor's `commits_without_story` judges)."""
    base = getattr(args, "base", None)
    return (strict._resolve_base(args.src, base) or base) if base is not None and cfg.mode == "strict" else base


def _decisions_path(dod_path, story_id, if_any=False):
    """The story's decisions file, which must exist (only `--add` creates it); with `if_any` a missing file is None (a no-op)."""
    path = decisions.decisions_file(dod_path, story_id)
    if not path.exists():
        if if_any:
            return None
        raise ValueError(f"no decisions file at {path}; record one with `ado decisions {story_id} --add \"<text>\" --kind decision`")
    return path


def _add_decision(args):
    """`decisions --add`: a local write of one line (creates the file). No token, no request."""
    path = decisions.decisions_file(Config.load().dod_path, args.id)
    d_id = decisions.apply_add(path, args.id, args.kind, args.add, args.why or "", args.supersedes)
    return {"id": d_id, "file": str(path)}


def _resolve_decision(args):
    """`decisions --resolve`: the PO's verdict is recorded by a human. Tested before anything is read or written."""
    if not (sys.stdin and sys.stdin.isatty()):
        raise ValueError("--resolve records the PO's verdict, which only a human may do: run it in your own terminal "
                         "(this shell has no terminal on stdin; a guard against accidents, not a security boundary)")
    path = _decisions_path(Config.load().dod_path, args.id)
    by, note = args.by if args.by is not None else "PO", args.note or ""
    try:
        masked = decisions.masked_by(decisions.parse_decisions(decisions._read(path)), args.resolve)
    except (OSError, UnicodeError):
        masked = None  # apply_resolve reports an unreadable file
    decisions.apply_resolve(path, args.resolve, args.verdict, by, note, args.force)
    return {"id": args.id, "resolved": args.resolve, "status": args.verdict, "by": by.strip(), "note": note.strip(),
            **({"notice": f"{args.resolve} is superseded by {masked}: this verdict is masked while {masked} stands "
                          f"(a rejected {masked} would bring it back)"} if masked else {})}


def _override(args):
    """`strict override`: a human records a `kind: override` decision that lets one strict rule pass. Tested before anything is
    read or written (no config, no file), with or without --apply."""
    if not (sys.stdin and sys.stdin.isatty()):
        raise ValueError("an override waives a strict rule, which only a human may do: run it in your own terminal "
                         "(this shell has no terminal on stdin; a guard against accidents, not a security boundary)")
    path = decisions.decisions_file(Config.load().dod_path, args.id)
    if args.apply:
        d_id = decisions.apply_override(path, args.id, args.rule, args.reason)
    else:
        try:
            md = decisions._read(path) if path.exists() else decisions.render_file(args.id)
        except (OSError, UnicodeError) as e:
            raise ValueError(f"cannot read the decisions file {path} ({e.__class__.__name__})")
        d_id = decisions.add_override(md, args.rule, args.reason)[1]
    return {"ok": True, "applied": bool(args.apply), "id": d_id, "rule": args.rule, "file": str(path),
            "line": decisions.render_line({"id": d_id, "kind": "override", "text": f"skip {args.rule}", "why": args.reason.strip(),
                                           "status": "open", "by": "", "note": "", "posted": None, "used": ""})}


def _config(args):
    """The config this run uses. With `--base REF` on `gate`, `strict check`, `strict verify`, `strict mode` or `branch --id-from`, the target branch's
    `ado.config.json` is read first: when it says `mode: strict`, the whole run uses it and the PR's own file is never read (a PR cannot
    weaken its own strictness); an unreadable one raises ConfigInvalid (fail closed). A target with no config, or an advisory one, leaves
    the PR's config in charge. A REF that is no commit here (a shallow clone, `origin/main` in a repo with no origin) cannot say what the
    target holds: with an advisory PR config that is the v0.2 path (the gate notes it and goes on); with a strict one, or an invalid one,
    it fails closed."""
    base = getattr(args, "base", None)
    if base is not None and (args.command in ("gate", "branch") or (args.command == "strict" and args.strict_command in ("check", "verify", "mode"))):
        repo = getattr(args, "src", None) or "."  # gate: the checkout --base belongs to
        try:
            target = Config.load_from_ref(base, repo=repo)
        except ConfigInvalid:
            if strict._resolve_base(repo, base):
                raise  # the base is a commit and its config is the problem
            head = Config.load()
            if head.mode == "strict":
                raise
            return head
        if target is not None and target.mode == "strict":
            target.from_target = True
            return target
    return Config.load()


def _stack_show(args):
    """`ado stack show`: the config only supplies the mode and strict.test_command (none is advisory; an invalid one fails closed)."""
    try:
        cfg = Config.load()
    except ConfigInvalid:
        raise
    except ConfigError:
        cfg = None
    result, approved, findings = stack.show(".", cfg.mode if cfg else "advisory", cfg.strict["test_command"] if cfg else None)
    print(stack.show_md(result, approved) if args.md else json.dumps(result, indent=2))
    if args.check and findings:
        print(f"error: stack check failed: {findings} finding(s)", file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ado", description="Azure DevOps work-item CLI")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="Configure this repo/team and verify access (read-only)")
    p = sub.add_parser("get", help="Get a work item (with relations)")
    p.add_argument("id", type=int)
    p = sub.add_parser("query", help="Run a WIQL query")
    p.add_argument("wiql")
    p = sub.add_parser("create", help="Create a work item")
    p.add_argument("--type", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--field", action="append", default=[], metavar="Name=Value")
    p = sub.add_parser("update", help="Update fields / transition state")
    p.add_argument("id", type=int)
    p.add_argument("--state")
    p.add_argument("--field", action="append", default=[], metavar="Name=Value")
    p = sub.add_parser("comment", help="Add a comment (System.History)")
    p.add_argument("id", type=int)
    p.add_argument("text")
    p = sub.add_parser("comments", help="Read a story's discussion (authors scrubbed); writes .ado/context/<id>.md")
    p.add_argument("id", type=int)
    p.add_argument("--out", metavar="PATH", help="markdown file (default: .ado/context/<id>.md)")
    p = sub.add_parser("link", help="Link to another work item, a parent, or a URL")
    p.add_argument("id", type=int)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--to", type=int, help="related work item")
    g.add_argument("--parent", type=int, help="set the parent work item")
    g.add_argument("--url")
    p = sub.add_parser("attach", help="Attach sensitive-data-safe doc files (.md/.svg/.txt)")
    p.add_argument("id", type=int)
    p.add_argument("files", nargs="+")
    p.add_argument("--comment", default="")
    p = sub.add_parser("attachments", help="List a story's attached files; --download saves the allowed ones (read-only)")
    p.add_argument("id", type=int)
    p.add_argument("--download", action="store_true",
                   help="save allowlisted files from ADO hosts only (5 MB cap each)")
    p.add_argument("--dir", help="download dir inside .ado/attachments/ (default: .ado/attachments/<id>)")
    p = sub.add_parser("branch", help="Name the local feature branch <branch_prefix>ab<id>-<slug> from the story "
                       "(dry run unless --apply; never pushes)")
    p.add_argument("id", type=int, nargs="?")
    p.add_argument("--apply", action="store_true", help="create, rename (a Spec Kit branch made for this spec, with no upstream) or "
                   "switch to the branch; the default branch is never renamed")
    p.add_argument("--id-from", metavar="NAME", help="print the story id the branch NAME is tied to (exit 1 if none)")
    p.add_argument("--base", metavar="REF", help="with --id-from: the ref the PR targets (CI: origin/<target>); when its ado.config.json is strict, "
                   "its branch_prefix is used, not the PR's")
    p.add_argument("--link", action="store_true", help="Azure Repos: add the pushed current branch to the story's "
                   "Development section (dry run unless --apply; needs a PAT with Code: Read and Project and Team: Read)")
    p.add_argument("--plan-out", metavar="PATH", help="with --link: write the markdown plan here")
    p.add_argument("--expect-plan", metavar="PATH",
                   help="with --link --apply: refuse (exit 2) unless the plan re-renders identical to this approved file")
    p = sub.add_parser("dod", help="Seed/refresh the DoD ledger (dod/DoD_<id>.md) from a story")
    p.add_argument("id", type=int)
    p.add_argument("--force", action="store_true", help="overwrite an existing ledger")
    p.add_argument("--refresh", action="store_true", help="re-pull section 1, preserve authored sections")
    p.add_argument("--dir", help="output dir (default: config dod_path)")
    p.add_argument("--spec-dir", help="Spec Kit feature dir recorded in the ledger (kept on --refresh)")
    p = sub.add_parser("questions", help="List the ledger's open and answered questions; --post sends the open ones "
                       "to the story as one comment (dry run unless --apply)")
    p.add_argument("id", type=int)
    p.add_argument("--post", action="store_true", help="plan posting the open questions as one comment (add or edit)")
    p.add_argument("--apply", action="store_true", help="with --post: perform the write (default: plan only)")
    p.add_argument("--plan-out", metavar="PATH", help="write the markdown plan here (implies --post)")
    p.add_argument("--expect-plan", metavar="PATH",
                   help="with --apply: refuse (exit 2) unless the plan re-renders identical to this approved file")
    p = sub.add_parser("decisions", help="List the open, validated and rejected D<n> lines of Decisions_<id>.md; --add "
                       "records one; --post sends the unposted open ones as one new comment (dry run unless --apply)")
    p.add_argument("id", type=int)
    p.add_argument("--post", action="store_true", help="plan posting the unposted decisions as one new comment")
    p.add_argument("--apply", action="store_true", help="with --post: post, then mark the ledger lines (default: plan only)")
    p.add_argument("--plan-out", metavar="PATH", help="write the markdown plan here (implies --post)")
    p.add_argument("--expect-plan", metavar="PATH",
                   help="with --apply: refuse (exit 2) unless the plan re-renders identical to this approved file")
    p.add_argument("--if-any", action="store_true", help="with --post/--plan-out: a missing decisions file is a no-op "
                   "(exit 0, nothing to post) instead of exit 2; the workflow's decisions-loop uses it")
    p.add_argument("--add", metavar="TEXT", help="record one assumption or decision: appends the next D<n> line to "
                   "<dod_path>/Decisions_<id>.md (created when missing). A local write; needs --kind")
    p.add_argument("--kind", help="with --add: decision or assumption")
    p.add_argument("--why", help="with --add: the reason (at most 300 characters)")
    p.add_argument("--supersedes", metavar="D<n>", help="with --add: this decision answers the rejected (or still open) D<n>: the "
                   "line starts with `supersedes D<n>: ` and the old line is left as it is")
    p.add_argument("--resolve", metavar="D<n>", help="a human records the PO's verdict on this line (local edit of the decisions file; "
                   "with --validated|--rejected, optional --by and --note). Refuses without a terminal so an agent "
                   "shell cannot do it by accident; this is not a security boundary, the strict gate and the git "
                   "history are the enforcement")
    p.add_argument("--force", action="store_true", help="with --resolve: overwrite the verdict of a resolved line")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--validated", dest="verdict", action="store_const", const="validated")
    g.add_argument("--rejected", dest="verdict", action="store_const", const="rejected")
    p.add_argument("--by", help="with --resolve: the role that decided (default PO; never a name)")
    p.add_argument("--note", help="with --resolve: a short note (at most 300 characters)")
    p = sub.add_parser("gate",
                       help="Advisory DoD gate: AC<->test traceability, assertions, AC drift, evidence")
    p.add_argument("id", type=int)
    p.add_argument("--src", default=".", help="repo dir to scan for existing tests")
    p.add_argument("--changed", nargs="*", default=[], help="changed files (PR diff)")
    p.add_argument("--base", metavar="REF",
                   help="ref the PR targets, e.g. origin/main: commits in REF..HEAD must reference the story (default: origin/HEAD)")
    p.add_argument("--offline", action="store_true", help="skip the ADO fetch (AC drift not checked)")
    p.add_argument("--summary-out", metavar="PATH", help="write a markdown summary here (also when the gate cannot run)")

    p = sub.add_parser("push", help="Mirror tasks.md into child Tasks under a story (dry run unless --apply)")
    p.add_argument("id", type=int, help="parent story id")
    p.add_argument("--from", dest="source", metavar="PATH", help="tasks.md (default: <ledger spec dir>/tasks.md)")
    p.add_argument("--apply", action="store_true", help="perform the writes (default: plan only, no writes)")
    p.add_argument("--plan-out", metavar="PATH", help="write the markdown plan here")
    p.add_argument("--expect-plan", metavar="PATH",
                   help="with --apply: refuse (exit 2) unless the plan re-renders identical to this approved file")

    p = sub.add_parser("done", help="Close a story if its DoD checklist is complete (dry run unless --apply)")
    p.add_argument("id", type=int)
    p.add_argument("--apply", action="store_true", help="attach the ledger and transition (default: plan only)")
    p.add_argument("--plan-out", metavar="PATH", help="write the markdown plan here")
    p.add_argument("--expect-plan", metavar="PATH",
                   help="with --apply: refuse (exit 2) unless the plan re-renders identical to this approved file")

    p = sub.add_parser("pr", help="Open/comment on a pull request (GitHub via gh, or Azure Repos)")
    psub = p.add_subparsers(dest="pr_command", required=True)
    q = psub.add_parser("create", help="Create a PR linked to a work item (dry run unless --apply)")
    q.add_argument("id", type=int, help="work item id (AB#<id> is ensured in the title)")
    q.add_argument("--title")
    q.add_argument("--body-file", metavar="F")
    q.add_argument("--base")
    q.add_argument("--head")
    q.add_argument("--from-prepared", action="store_true",
                   help="read title/base/head from .ado/pr-args.json and the body from .ado/pr-body.md")
    q.add_argument("--push", action="store_true",
                   help="with --from-prepared --apply: `git push -u origin <head>` before creating the PR")
    q.add_argument("--allow-incomplete", action="store_true",
                   help="open the PR as a draft when tasks.md has unticked tasks (default: refuse)")
    q.add_argument("--apply", action="store_true", help="create the PR (default: plan only, no writes)")
    q.add_argument("--plan-out", metavar="PATH", help="write the markdown plan here")
    q.add_argument("--expect-plan", metavar="PATH",
                   help="with --apply: refuse (exit 2) unless the plan re-renders identical to this approved file")
    q = psub.add_parser("comment", help="Comment on an Azure Repos PR")
    q.add_argument("pr_id", type=int)
    q.add_argument("--body-file", required=True, metavar="F")

    p = sub.add_parser("stack", help="Which stacks the repository holds (read-only, local): `stack detect` reads the files tracked at HEAD")
    stsub = p.add_subparsers(dest="stack_command", required=True)
    q = stsub.add_parser("detect", help="Detect the stack(s) from the files committed at HEAD (never the working tree) and show why: JSON "
                         "{outcome, stacks, domain_hints}; no commit yet is `unknown` with a note; exit 2 when git cannot be read")
    q.add_argument("--repo", default=".", metavar="DIR", help="the repository (default: the current directory)")
    q = stsub.add_parser("show", help="The stack profile `ado.stack.json` as status, counts and fixed error texts (JSON, the default); `--md` prints an "
                         "APPROVED profile as data for the agent; the text of a draft or changed profile is never printed; `--check` exits 2 in "
                         "strict mode unless the profile is approved, matches strict.test_command and covers every high-confidence root")
    fmt = q.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="JSON (default)")
    fmt.add_argument("--md", action="store_true", help="markdown (the profile text only when approved)")
    q.add_argument("--check", action="store_true", help="strict mode: exit 2 when there is a finding (advisory: always 0)")

    p = sub.add_parser("strict", help="Strict SDD+TDD mode: phase checks (read-only) and `verify` (runs the tests, records the result)")
    ssub = p.add_subparsers(dest="strict_command", required=True)
    q = ssub.add_parser("check", help="Check the strict-mode rules of a phase (read-only; exit 2 lists the findings as JSON)")
    q.add_argument("id", type=int, help="story id")
    q.add_argument("--phase", required=True, choices=sorted(strict.PHASES), help="implement, pr or done")
    q.add_argument("--base", metavar="REF", help="pr: the ref the PR targets, e.g. origin/main (resolved as refs/remotes/REF, refs/heads/REF, "
                   "then a full commit id, never a tag); red and green must lie in REF..head (an unknown REF is the finding base-unknown; "
                   "default: the branch origin/HEAD points at)")
    q.add_argument("--head", metavar="REF", default="HEAD", help="the branch or commit the PR is made from (default HEAD; CI passes the head SHA, "
                   "so a detached checkout works)")
    q = ssub.add_parser("mode", help="Which mode CI judges a PR by (read-only, no token, no network): prints {\"mode\", \"source\"}; exit 3 when the "
                        "config cannot be trusted (CI fails closed)")
    q.add_argument("--base", metavar="REF", help="the ref the PR targets, e.g. origin/main: a strict config there decides (source: target branch), "
                   "else the PR's own config (source: pull request)")
    q = ssub.add_parser("override", help="A human, in their own terminal, records a `kind: override` decision that lets ONE strict rule pass for "
                        "this story (a local write of Decisions_<id>.md; refuses without a terminal; not a security boundary; the entry must be posted "
                        "before `pr`); without --apply prints the line it would record")
    q.add_argument("id", type=int, help="story id")
    q.add_argument("rule", choices=decisions.OVERRIDE_RULES, help="the rule to skip: test-command, l2 (the record and test-run rules), open-question or placeholder")
    q.add_argument("--reason", required=True, metavar="TEXT", help="why, 20 to 300 characters (no control characters, `<!--`, `-->` or code fences)")
    q.add_argument("--apply", action="store_true", help="write the line (default: print it)")
    q = ssub.add_parser("verify", help="Run L1, then strict.test_command at each AC's red (must fail) and green (must pass) commit; "
                        "records the result in ledger section 4 (exit 2 lists the findings as JSON)")
    q.add_argument("id", type=int, help="story id")
    rec = q.add_mutually_exclusive_group()
    rec.add_argument("--record", dest="record", action="store_true", default=True,
                     help="append the verification record to the ledger (default)")
    rec.add_argument("--no-record", dest="record", action="store_false", help="write nothing, only set the exit code (CI)")
    q.add_argument("--base", metavar="REF", help="refuse a red or green commit that is already in REF (it must be this branch's own work)")
    q.add_argument("--allow-override", action="store_true",
                   help="with --no-record: accept an override in force (default: it is the finding override-in-force, since it skips the run)")

    args = parser.parse_args(argv)
    if getattr(args, "expect_plan", None) and not args.apply:
        parser.error("--expect-plan needs --apply")
    if args.command in ("questions", "decisions") and args.apply and not args.post:
        parser.error("--apply needs --post")
    if args.command == "decisions":
        if args.add is not None and (args.post or args.apply or args.plan_out or args.expect_plan or args.resolve
                                     or args.verdict or args.by is not None or args.note is not None or args.force):
            parser.error("--add cannot be combined with --post, --apply, --plan-out, --expect-plan or --resolve options")
        if args.add is not None and args.kind is None:
            parser.error("--add needs --kind decision|assumption")
        if args.add is None and (args.kind is not None or args.why is not None or args.supersedes is not None):
            parser.error("--kind, --why and --supersedes need --add")
        if args.if_any and (args.add is not None or args.resolve or not (args.post or args.plan_out)):
            parser.error("--if-any needs --post or --plan-out and cannot be combined with --add or --resolve")
        if args.resolve and (args.post or args.apply or args.plan_out or args.expect_plan):
            parser.error("--resolve cannot be combined with --post, --apply, --plan-out or --expect-plan")
        if args.resolve and not args.verdict:
            parser.error("--resolve needs --validated or --rejected")
        if not args.resolve and (args.verdict or args.by is not None or args.note is not None or args.force):
            parser.error("--validated, --rejected, --by, --note and --force need --resolve")
    if getattr(args, "dir", None) and args.command == "attachments" and not args.download:
        parser.error("--dir needs --download")
    if args.command == "branch":
        if (args.id is None) == (args.id_from is None):
            parser.error("give a story id or --id-from NAME (not both)")
        if args.base is not None and args.id_from is None:
            parser.error("--base needs --id-from")
        if args.id_from is not None and (args.apply or args.link):
            parser.error("--id-from takes no --apply or --link")
        if (args.plan_out or args.expect_plan) and not args.link:
            parser.error("--plan-out and --expect-plan need --link")
        if args.id is not None and not 1 <= args.id <= prs.MAX_ID:
            parser.error(f"the story id must be between 1 and {prs.MAX_ID}")
    err = None
    try:
        if args.command == "setup":
            return run_setup()
        if args.command == "decisions" and args.add is not None:  # local only: no token, no request
            out = _add_decision(args)
        elif args.command == "decisions" and args.resolve:  # local only: no token, no request
            out = _resolve_decision(args)
        elif args.command == "stack" and args.stack_command == "show":  # local only: no token, no request
            return _stack_show(args)
        elif args.command == "stack":  # local only: no token, no request, no config
            out = stack.detect(args.repo)
        elif args.command == "strict" and args.strict_command == "override":  # local only: no token, no request
            out = _override(args)
        elif args.command == "strict" and args.strict_command == "mode":  # local only: no token, no request
            cfg = _config(args)
            out = {"mode": cfg.mode, "source": "target branch" if cfg.from_target else "pull request"}
        elif args.command == "branch" and args.id_from is not None:  # local only: no token, no request
            out = prs.story_from_branch(args.id_from, _config(args).branch_prefix)
        else:
            client = AdoClient(_config(args))
            out = dispatch(client, args)
            if args.command == "gate" and client.config.from_target:  # additive: only when the target's config judged this run
                out["config"] = gate.TARGET_CONFIG_NOTE
            elif args.command == "gate" and args.base is not None and client.config.mode == "strict":  # a strict PR config under --base
                out["config"] = gate.PR_CONFIG_NOTE
    except (ConfigError, AuthError, dod.DodExists, ValueError) as e:
        # 3 (invalid config) lets CI fail closed: `gate`, and `branch --id-from` (the Azure gate reads its story id with it)
        fail_closed = args.command == "gate" or (args.command == "branch" and args.id_from is not None) or (
            args.command == "strict" and args.strict_command == "mode")
        err, code = e, 3 if fail_closed and isinstance(e, ConfigInvalid) else 2
    except AdoError as e:
        err, code = e, 1
    if err is not None:
        if isinstance(err, strict.Refused):
            print(json.dumps(err.check, indent=2))  # the findings, as `strict check` prints them
        print(f"error: {err}", file=sys.stderr)
    else:
        print(json.dumps(out, indent=2))
    if getattr(args, "summary_out", None):  # after the JSON, so a write failure cannot hide a blocking result
        summary = Path(args.summary_out)
        try:
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text(gate.render_error(err) if err is not None else gate.render_summary(out), encoding="utf-8")
        except OSError:  # advisory output only: keep the gate's own exit code
            print("warning: could not write the --summary-out file", file=sys.stderr)
    if err is not None:
        return code
    if args.command == "strict" and args.strict_command != "mode" and not out["ok"]:
        print(f"error: strict {args.strict_command} failed: {len(out['findings'])} finding(s)", file=sys.stderr)
        return 2
    if args.command == "branch" and out is None:
        return 1
    if args.command == "pr" and out.get("error"):
        return 1
    if args.command == "attach" and out["failed"]:
        return 1
    if args.command == "attachments" and any("error" in a for a in out):
        return 1
    if args.command == "push" and out.get("failed"):
        return 1
    if args.command == "done" and (not out["ready"] or out.get("failed") or out.get("error")):
        return 1
    if args.command == "gate" and out.get("blocking"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
