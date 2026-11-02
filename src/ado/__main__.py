import argparse
import json
import os
import sys
from datetime import date

from pathlib import Path

from . import __version__, approvals, attachments, branch, calibrate, comments, decisions, dod, done, doctor, gate, kits, prs, push, questions, rules, stack, status, strict, tasks, workitems
from .auth import AuthError
from .client import AdoClient, AdoError
from .config import Config, ConfigError, ConfigInvalid, trusted
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


def _pr_trust(base):
    """The trust that judges `pr create --apply`: the stricter of the PR's target branch (`--base`, when it resolves to a commit here) and `origin/HEAD`.
    Comment mode from either wins; with both in comment mode the approvers must be the same set (two lists cannot be ordered: refuse). When the base does
    not resolve, `origin/HEAD` alone (as every other apply). An unreadable target config raises (exit 2)."""
    home = trusted(".")
    ref = strict.base_ref(".", base)
    if not strict._resolve_base(".", ref):
        return home
    target = trusted(".", base=ref)
    strict_ones = [t for t in (home, target) if t.approval == "comment"]
    if not strict_ones:
        return home
    if len(strict_ones) == 2 and set(home.approvers) != set(target.approvers):
        raise ValueError("comment approval: the default branch and the PR's target branch name different approvers, so it is unclear who may approve; "
                         "make `approvers` the same on both (docs/configuration.md, 'Approvers and comment mode')")
    return strict_ones[0]


def _approved(client, args, verb, text, writes=True, trust=None):
    """`--apply` in `approval: "comment"` mode (P4-12a): after `_plan` has bound the apply to the approved preview and before the first write, a trusted
    approver's `/approve <gate> <hash>` comment on the story must name this plan (`approvals.require`; the hash covers the rendered plan `text`, UTF-8).
    The trust keys come only from the target branch. A plan that would write nothing needs no approval. Not used by `done`, `strict override`, `--force`
    or the single-item verbs."""
    if args.apply and writes:
        approvals.require(client, trusted(".") if trust is None else trust, args.id, verb, text.encode("utf-8"))


def _push(client, args):
    path = _tasks_path(client, args)
    try:
        parsed = tasks.parse_tasks(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read {path}: {e}")
    ops = push.plan_push(client, args.id, parsed)  # read-only: GET/WIQL only
    text = push.render_plan(args.id, ops)
    _plan(args, text)
    _approved(client, args, "push", text, any(o["action"] in ("create", "update") for o in ops))
    result = push.apply_push(client, args.id, ops) if args.apply else {}
    return {"parent": args.id, "from": str(path), "applied": args.apply, "ops": ops, **result}


def _stamp_used(client, args, ids):
    """The apply paths, after the strict check passed and just before anything is sent: stamp `used` on the overrides it relied on.
    A failed write is exit 2 with nothing sent."""
    if ids:
        decisions.apply_stamp(decisions.decisions_file(client.config.dod_path, args.id), args.id, ids)


def _xcheck(client, args, dec):
    """(findings, blocking) of `done`'s cross-check (D§4.4): none unless the TARGET branch (`--base`, else origin/HEAD) configures approvers; an unreadable
    target config raises (exit 2: nothing is believed). It blocks when either this config or the target's is strict: a PR that sets `mode: advisory` in
    the same change must not turn its own cross-check into a warning. The decisions file `dec` was already confined and read by the caller."""
    approvers = trusted(".", base=args.base).approvers
    if not approvers:
        return [], False
    base = args.base or strict.default_base(".")
    target = Config.load_from_ref(base, repo=".") if base else None
    blocking = client.config.mode == "strict" or (target is not None and target.mode == "strict")
    md = (gate._read(dec, ".", set()) or "") if os.path.lexists(dec) else ""
    return strict.xcheck_run(client, args.id, md, approvers), blocking


def _done(client, args):
    if args.base is not None and not args.base:
        raise ValueError("--base must not be empty")  # an empty base would fall back to origin/HEAD, the merged branch, whose config the PR wrote
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
    xf, blocking = _xcheck(client, args, dec)
    if xf and blocking:
        raise strict.Refused({"phase": "done", "mode": "strict", "ok": False, "findings": xf, "overrides_used": []})
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
            **({"decisions_file": str(dec)} if dec else {}), **({"warnings": [f"{f['rule']}: {f['detail']}" for f in xf]} if xf else {}), **result}


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
    text = prs.render_plan(host, title, prs.azure_description(body) if azure else body, args.base, args.head, [args.id], tasks_row)
    _plan(args, text)
    info = {"applied": args.apply, "host": host, "title": title, "base": args.base, "head": args.head}
    if not args.apply:
        return info
    _approved(client, args, "pr-create", text, trust=_pr_trust(args.base))
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
        res = prs.github_create(title, body, args.base, args.head, draft)
    return res if res.get("error") else {**info, **res}


def dispatch(client, args):
    if args.command == "strict":
        if args.strict_command == "verify":
            return strict.verify(client, args.id, record=args.record, base=args.base, allow_override=args.allow_override)
        base = args.base
        if base is None and args.phase == "pr" and client.config.mode == "strict":
            base = strict.default_base(".")  # "" when origin/HEAD is not set: the finding base-unknown, never a pass
        return strict.check(client, args.id, args.phase, head=args.head, base=base, target=args.base)
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
            text = branch.render_link_plan(p)
            _plan(args, text)
            do = args.apply and p["action"] == "link"
            if do:
                _approved(client, args, "branch-link", text)
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
        rules_cfg = client.config.rules
        # the walk (more GETs) only when rules are on; an ancestor GET error propagates before anything is written
        rules_from = [a["id"] for a in rules.walk(client, args.id, rules_cfg["types"])] if rules_cfg["enabled"] else None
        path = dod.write_dod(
            wi, dod_dir, client.config.organization, client.config.project,
            synced=date.today().isoformat(), force=args.force, refresh=args.refresh,
            spec_dir=getattr(args, "spec_dir", None) or "",
            extra_dod=dod.stack_dod_items(stack.load_profile()),  # approved profile only; write_dod ignores it on --refresh
            rules_from=rules_from,
        )
        action = "refreshed" if (args.refresh and existed) else ("overwritten" if existed else "created")
        return {"path": str(path), "action": action, **dod.ac_stats(wi)}
    if args.command == "questions" and getattr(args, "sync", False):  # reads ADO (GET only), writes only the local ledger
        return _sync_questions(client, args)
    if args.command == "questions":
        path = dod.dod_file(client.config.dod_path, args.id)
        try:
            parsed = questions.parse_questions(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"cannot read ledger {path} ({e.__class__.__name__}); run `ado dod {args.id}` first")
        if not (args.post or args.plan_out):
            return parsed
        plan = questions.plan_post(client, args.id, parsed)  # read-only: GET only
        text = questions.render_plan(args.id, plan, parsed["unparsed"])
        _plan(args, text)
        _approved(client, args, "questions-post", text, plan["action"] in ("add", "edit"))
        if args.apply:
            questions.apply_post(client, args.id, plan)
        return {"id": args.id, "applied": args.apply, "action": plan["action"], "comment_id": plan["comment_id"]}
    if args.command == "decisions" and args.sync:  # reads ADO (GET only), writes only the local decisions file
        return _sync_decisions(client, args)
    if args.command == "decisions":  # read-only unless --post/--plan-out; only --apply writes (comment, then the file)
        path = _decisions_path(client.config.dod_path, args.id, args.if_any)  # None only with --if-any and no file
        try:
            parsed = decisions.parse_decisions(path.read_text(encoding="utf-8") if path else "")
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"cannot read the decisions file {path} ({e.__class__.__name__})")
        if not (args.post or args.plan_out):
            return parsed
        plan = decisions.plan_post(client, args.id, parsed)
        text = decisions.render_plan(args.id, plan, parsed["unparsed"])
        _plan(args, text)
        _approved(client, args, "decisions-post", text, plan["action"] == "add")
        res = decisions.apply_post(client, args.id, plan, path) if args.apply else {}
        return {"id": args.id, "applied": args.apply, "action": plan["action"], "lines": plan["lines"],
                "comment_id": res.get("comment_id"), **({"note": "no decisions file; nothing to post"} if path is None else {})}
    if args.command == "rules":
        try:
            return rules.pull(client, args.id)  # GETs only; local files; ids, counts and keys out
        except AuthError as e:
            raise AdoError(str(e))
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
        rules_ctx = _gate_rules_ctx(client.config, ledger_md, args.src)
        if rules_ctx is not None:  # the live check: GETs only, from the story id given here (never an id read from the ledger or a file)
            rules_ctx["drift"] = rules.DRIFT_OFFLINE if getattr(args, "offline", False) else rules.drift(
                client, args.id, client.config, ledger_md, rules_ctx["files"], decisions.parse_decisions(decisions_md or ""))
        if rules_ctx is not None and getattr(args, "base", None) is not None:  # CI: the target branch's own ledger (hardened git read), judged from the story id given here
            rules_ctx["reconfirmed"] = strict.reconfirmed_vs_base(client.config.dod_path, args.src, args.base, args.id,
                                                                  dod.ledger_rules_reviewed(ledger_md)["hashes"])
        skipped = set()
        sources = gate.discover_tests(args.src, tests, skipped)
        changed = {f: t for f in (args.changed or []) if (t := gate._read(f, args.src, skipped)) is not None}
        return gate.run_gate(ledger_md, sources, changed, work_item, skipped=len(skipped),
                             block=client.config.effective_gate_block(), tests=tests, reason=reason,
                             commits=gate.check_commits(args.src, args.id, _gate_base(client.config, args)),
                             decisions_md=decisions_md, decisions_unreadable=unreadable, rules_ctx=rules_ctx)
    raise SystemExit(2)


def _gate_rules_ctx(cfg, ledger_md, src):
    """None when rules are off (nothing is read: the v0.2 gate exactly). Else the parsed rules files that the ledger's `Rules from:` names
    (`rules.local_files`: confined reads, digits-only ids; an unreadable one is simply absent, and the gate reports it as missing)."""
    return {"files": rules.local_files(cfg, ledger_md, src)} if cfg.rules["enabled"] else None


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


def _sync_decisions(client, args):
    """`decisions --sync`: approvers' `/approve D<n>` and `/reject D<n>` comments become verdicts in the local decisions file (no ADO write, no gate:
    `rules/hitl.md`, local write). Every refusal (exit 2, fixed text, nothing written) comes before anything is changed: no approvers on the target branch,
    an unreadable decisions file, a token that can bypass rules (or whose permission cannot be read), comments that were cut off or cannot be trusted."""
    approvers = trusted(".").approvers
    if not approvers:
        raise ValueError("--sync needs approvers on the target branch (`approvers` in ado.config.json there, see docs/configuration.md); none are configured")
    path = decisions.decisions_file(client.config.dod_path, args.id)
    if not os.path.lexists(path) or gate._read(path, ".", set()) is None:
        raise ValueError("cannot read the decisions file (missing, a symlink, outside the working tree, over 1 MiB or not UTF-8); nothing written")
    approvals.comment_mode_allowed(client)
    try:
        found, truncated = comments.fetch(client, args.id, with_meta=True)
    except comments.CommentsUnreadable:
        raise ValueError("the story's comments cannot be read reliably (an unexpected answer from ADO); nothing written") from None
    if truncated:
        raise ValueError("not every comment of the story was read (page limit or repeated page token), so a newer retraction could be missing; nothing written")
    try:
        excluded = approvals.excluded(client, found)
    except ValueError:  # `questions._me` puts the client's error text into its message
        raise ValueError("cannot tell which ADO identity this token is, so the comments cannot be attributed; nothing written") from None
    report = decisions.apply_sync(path, found, approvers, excluded, client, args.id)
    notice = [f"{i}: {n} later malformed command line(s) by an approver: check the thread" for i, n in report["bad_newer"].items() if n]
    notice += [f"{i} is superseded by {m}: this verdict is masked while {m} stands (a rejected {m} would bring it back)" for i, m in report["masked"].items()]
    return {"id": args.id, **report, **({"notice": notice} if notice else {})}


def _sync_questions(client, args):
    """`questions --sync`: approvers' `/answer Q<n>: text` comments become the PO's answers in the local ledger (no ADO write, no gate: `rules/hitl.md`, local
    write) and the questions that have waited `unattended.assume_after_hours` (by SERVER time) are listed as due; nothing is closed here. Every refusal (exit 2,
    fixed text, nothing written) comes before anything is changed: no approvers on the target branch, an unreadable or malformed ledger, a token that can bypass
    rules (or whose permission cannot be read), comments that were cut off or cannot be trusted, no way to tell how long a question has been asked, no server time."""
    trust = trusted(".")
    approvers = trust.approvers
    if not approvers:
        raise ValueError("--sync needs approvers on the target branch (`approvers` in ado.config.json there, see docs/configuration.md); none are configured")
    path = dod.dod_file(client.config.dod_path, args.id)
    md = gate._read(path, ".", set()) if os.path.lexists(path) else None
    if md is None:
        raise ValueError("cannot read the ledger (missing, a symlink, outside the working tree, over 1 MiB or not UTF-8); nothing written")
    parsed = questions.check_ledger(md)
    approvals.comment_mode_allowed(client)
    try:
        found, truncated = comments.fetch(client, args.id, with_meta=True)
    except comments.CommentsUnreadable:
        raise ValueError("the story's comments cannot be read reliably (an unexpected answer from ADO); nothing written") from None
    if truncated:
        raise ValueError("not every comment of the story was read (page limit or repeated page token), so a newer answer could be missing; nothing written")
    try:
        excluded = approvals.excluded(client, found)
    except ValueError:  # `questions._me` puts the client's error text into its message
        raise ValueError("cannot tell which ADO identity this token is, so the comments cannot be attributed; nothing written") from None
    hours = trust.unattended.get("assume_after_hours")
    ages = questions.ages(client, args.id, parsed["open"], found)
    now = None
    if hours is not None:
        try:
            now = client.server_now()
        except AdoError:
            now = None
        if now is None:
            raise ValueError(questions.E_TIME)
    report = questions.apply_sync(path, md, found, approvers, excluded, client, args.id, now, hours, ages)
    notice = [f"{i}: {n} later malformed command line(s) by an approver: check the thread" for i, n in report["bad_newer"].items() if n]
    notice += [f"{i}: answered already: a later /answer was not applied; correct the ledger by hand" for i in report["ignored"]]
    return {"id": args.id, **report, **({"notice": notice} if notice else {})}


E_NOT_COMMENT_MODE = "terminal mode (`approval` is not \"comment\" on the target branch): nothing to sync"
E_NO_DECISIONS_FILE = "there is no decisions file for this story: nothing to sync"


def _comment_mode(trust):
    """True when the TARGET branch's `approval` is "comment": the one condition `approvals check` and the `--if-comment-mode` sync steps share."""
    return trust.approval == "comment"


def _approvals_check(args):
    """`approvals check ID --gate G` (P4-12b): terminal mode is a no-op that reads nothing from ADO (not even a token); comment mode needs the approvers' comments
    (`approvals.check`). Exit 2 with a fixed text when the gate is unknown, not checkable here or not met."""
    if args.gate not in approvals.GATES:
        raise ValueError(approvals.E_GATE)
    trust = trusted(".")
    if not _comment_mode(trust):
        return approvals.check(None, trust, args.id, args.gate, None)
    client = AdoClient(_config(args))
    return approvals.check(client, trust, args.id, args.gate, client.config)


def _approvals_sync(args):
    """`approvals sync RUN_ID [--resume] [--dry-run]` (P4-13, the poller): comment mode only. The run id is checked first, then the target branch's trust: in terminal
    mode it exits 2 with a fixed text before any token is read or any request is made. The rest is `approvals.sync`."""
    if not approvals.valid_run_id(args.run_id):
        raise ValueError(approvals.E_RUN_ID)
    trust = trusted(".")
    if trust.approval != "comment":
        raise ValueError(approvals.E_SYNC_TERMINAL)
    client = AdoClient(_config(args))
    return approvals.sync(client, trust, args.run_id, client.config, ".", resume=args.resume, dry_run=args.dry_run)


def _resolve_decision(args):
    """`decisions --resolve`: the PO's verdict is recorded by a human. Tested before anything is read or written."""
    if trusted(".").approvers:
        raise ValueError("--resolve is refused: approvers are configured on the target branch, so verdicts come from their `/approve D<n>` and "
                         "`/reject D<n>` comments: run `ado decisions <id> --sync`")
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
    weaken its own strictness), and the target's `ado.stack.json` and `ado.kits.lock.json` are read and validated too; an unreadable
    or invalid one raises ConfigInvalid (fail closed). A target with no config, or an advisory one, leaves
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
            strict.read_target_trust(target, repo)  # the profile and the lockfile of the target commit; ConfigInvalid fails closed
            return target
    return Config.load()


def _kits_verify(args):
    """`ado kits verify`: local and read-only (no token, no config). JSON; exit 2 on a kit that is unpinned or changed, or when the registry
    or a file cannot be trusted (a fixed error text; a bad lockfile is ConfigInvalid, exit 2 here too)."""
    try:
        out = kits.verify(".", kits.load_lock("."))
    except kits.KitError as e:
        print(f"error: kits check failed: {e.reason}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    if out["findings"]:
        print(f"error: kits verify failed: {len(out['findings'])} finding(s)", file=sys.stderr)
        return 2
    return 0


def _kits_pin(args):
    """`ado kits pin ID [--by ROLE] [--apply]`: a human's approval record of one installed kit in `ado.kits.lock.json`. The preview prints the
    digests, the scan facts and the fixed checklist only (never kit text, so any output is safe). `--apply` needs a terminal on BOTH stdin
    and stdout, tested before any file is read (a guard against accidents, not a security boundary: a pseudo-terminal gives a tty); it
    prints that preview, then `approval code: <12 hex>`, and pins only when the human types the code (the code covers the lock entry; the
    kit is read again and the code recomputed right before the write)."""
    if args.apply and not (sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty()):
        raise ValueError("pinning a kit records that a human reviewed it, which only a human may do: run it in your own terminal "
                         "(stdin and stdout must both be a terminal, so the full preview is seen; a guard against accidents, not a security boundary)")
    try:
        prior = kits.load_lock(".")  # a bad lockfile is refused here too, in the preview as well as at --apply
        plan = kits.plan_pin(".", args.id)
        shown = {k: v for k, v in plan.items() if k != "source"}  # the manifest's URL is kit text: it is recorded, not printed
        out = {"applied": False, **shown, "source_recorded": plan["source"] is not None}
        if args.id in prior["kits"]:
            out["note"] = "this kit is already pinned: --apply REPLACES its entry (version, digests, scan, reviewer)"
        if args.apply:
            print(json.dumps(out, indent=2))
            code = kits.pin_code(plan, args.by)
            if not stack.confirm_code(code):
                raise ValueError(stack.E_NOT_CONFIRMED)
            kits.apply_pin(".", plan, args.by, code)
            out["applied"] = True
    except kits.KitError as e:
        print(f"error: kits pin refused: {e.reason}" + (kits.SYMLINK_HINT if e.reason == "symlink" else ""), file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))  # after --apply: the pinned digests (dir_digest, agents_sha256) as written
    return 0


def _doctor(args):
    """`ado doctor [--json] [--probe-item ID]`: rows of what is wrong with this setup (read-only; the one network call is a GET of connectionData).
    Exit 0 when no row fails, else 1. A token, name or email never appears."""
    result = doctor.run(".", probe_item=args.probe_item)
    print(json.dumps(result, indent=2) if args.json else doctor.render(result))
    return 1 if doctor.failed(result) else 0


def _status(args):
    """`ado status ID [--json] [--offline] [--catch-up]`: the first unmet stage of the story and its one next command (read-only; the board stages
    need the network and are 'not checked' offline or when ADO cannot be read). Exit 0: it reports, it does not judge. No ADO text is printed."""
    try:
        cfg = Config.load()
    except ConfigError:
        cfg = None  # the `config` stage says so
    client = None if args.offline or cfg is None else AdoClient(cfg)
    result = status.evaluate(client, args.id, ".", offline=args.offline, with_catch_up=args.catch_up)
    print(json.dumps(result, indent=2) if args.json else status.render(result, args.id))
    return 0


def _calibrate(args):
    """`ado calibrate (--emit PATH | --score FILE)`: local only (no token, no network, no model). `--emit` writes the plane 8 rubric and the 12
    unlabelled golden items under `.ado/`; `--score` reads a model's answer (under `.ado/`, at most 64 KiB) and exits 0 only at agreement >= 10 of 12
    with no false pass, else 1. A refused file or answer is exit 2 with a fixed text, never a piece of the answer."""
    try:
        if args.emit is not None:
            path = calibrate.ado_path(args.emit, ".md")
            items = calibrate.load_items()
            text = calibrate.emit(calibrate.read_review("."), items)
            calibrate.write_emit(path, text)
            print(json.dumps({"emitted": os.path.relpath(path), "items": len(items), "bytes": len(text.encode("utf-8"))}, indent=2))
            return 0
        result = calibrate.score(calibrate.load_items(), calibrate.read_answer(calibrate.ado_path(args.score)))
    except (ValueError, OSError) as e:
        print(f"error: {e if isinstance(e, ValueError) else 'the calibration file cannot be written'}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def _rules_confirm(args):
    """`ado rules <id> --confirm`: records in the ledger header the rule hashes a human reviewed (local only: no token, no request). New ids
    are added by anyone, also when another id changed or was dropped (written first, in one atomic write, then exit 2 naming the two lists). An
    already-recorded id that would get another hash, or leave the record, is a human re-reading a changed rule: it
    needs a terminal on stdin AND stdout (a guard against accidents, not a security boundary: a pseudo-terminal gives a tty), prints the ids
    and old/new hashes, and writes only when the typed `approval code` matches; everything is read again and the code recomputed right
    before the write."""
    if os.environ.get("ADO_CONFIG"):
        raise ValueError(rules.CONFIRM_ENV)
    dod_path = Config.load().dod_path
    prep = rules.prepare_confirm(dod_path, args.id)
    if prep["needs_human"]:
        if not (sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty()):
            prep["write_free"]()  # the new ids are free: recorded in one atomic write before the refusal for the rest
            raise ValueError(rules.confirm_refusal(prep))
        print(rules.confirm_preview(args.id, prep))
        if not stack.confirm_code(prep["code"]):
            raise ValueError(stack.E_NOT_CONFIRMED)
        again = rules.prepare_confirm(dod_path, args.id)
        if again["code"] != prep["code"]:
            raise ValueError(rules.CONFIRM_SWAPPED)
        prep = again
    written = prep["write"]()
    return {"id": args.id, "reviewed": prep["reviewed"], "added": prep["added"], "changed": prep["changed"], "removed": prep["removed"], "written": written}


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


def _stack_init(args):
    """`ado stack init`: a draft `ado.stack.json` from a seed or a file under `.ado/`. Local write, no ADO call, no gate: the draft is not
    trusted until approved. Output is counts and fixed error texts, never profile text."""
    plan = stack.plan_init(args.seed if args.seed is not None else stack.read_source(".", args.from_file), ".", args.stack, args.root)
    out = {"ok": plan["ok"], "errors": plan["errors"], "counts": plan["counts"]}
    if not plan["ok"]:
        print(json.dumps(out, indent=2))
        print(f"error: the profile is invalid ({len(plan['errors'])} error(s)); nothing written", file=sys.stderr)
        return 2
    for warning in stack.init_warnings(plan, args.seed):
        print(f"warning: {warning}", file=sys.stderr)
    if args.apply:
        out.update(stack.apply_init(".", plan, args.force))
        if args.from_file is not None and os.path.realpath(args.from_file) == os.path.realpath(stack.DRAFT_FILE):
            try:
                os.unlink(stack.DRAFT_FILE)
            except OSError:
                print("warning: could not remove .ado/stack-draft.json", file=sys.stderr)
    print(json.dumps(out, indent=2))
    return 0


def _timeout_info(cfg, command):
    """The `strict.test_timeout` sentence of the approve summary (no behaviour change; a flat string, so the summary stays one JSON object); None when no command is written."""
    return None if command is None else stack.timeout_text(cfg.strict["test_timeout"] if cfg else None)


def _stack_approve(args):
    """`ado stack approve`: the human's approval. `--apply` needs a terminal on stdin AND stdout, tested before any file or the config is
    read (a guard against accidents, not a security boundary: a pseudo-terminal gives a tty). It prints the full preview, then `approval
    code: <12 hex>`, and approves only when the human types the code; the profile is read again and the code recomputed right before the
    write. A terminal preview prints the profile's commands and rules in full (printed, never stored) when stdin and stdout are both
    terminals; anything else prints counts and lengths only, so unapproved research text never reaches an agent's shell."""
    shown = bool(sys.stdin and sys.stdin.isatty()) and bool(sys.stdout and sys.stdout.isatty())  # the full text only to a terminal: not into a pipe or a file
    if args.apply and not shown:
        raise ValueError("approving a stack profile changes what strict CI runs, which only a human may do: run it in your own terminal "
                         "(stdin and stdout must both be a terminal, so the full preview is seen; a guard against accidents, not a security boundary)")
    if args.apply:
        if os.environ.get("ADO_CONFIG"):
            raise ValueError(stack._E_ENV)
        cfg = Config.load()
        prep = stack.prepare_approve(".", "ado.config.json", args.by, args.story, cfg.dod_path)
        plan = prep["plan"]
        print(stack.preview_text(plan["profile"], plan["test_command"], prep["config_command"], plan["version"], args.story, args.by, plan["status"],
                                 timeout=cfg.strict["test_timeout"]))
        if not stack.confirm_code(prep["code"]):
            raise ValueError(stack.E_NOT_CONFIRMED)
        res = stack.apply_approve(".", "ado.config.json", args.by, args.story, cfg.dod_path, prep["code"])
        print(json.dumps({"applied": True, "version": res["version"], "sha256": res["sha256"], "by": res["by"], "date": res["date"],
                          "decision": res["decision"], "test_command": res["config"],
                          "command": stack._clean(res["test_command"], stack.MAX_PROFILE, markup=False) if res["test_command"] is not None else None,
                          "test_timeout": _timeout_info(cfg, res["test_command"])}, indent=2))
        return 0
    try:
        cfg = Config.load()
    except ConfigInvalid:
        raise
    except ConfigError:
        cfg = None
    plan = stack.plan_approve(".", cfg)
    if shown:
        print(stack.preview_text(plan["profile"], plan["test_command"], plan["config_command"], plan["version"], args.story, args.by,
                                 plan["status"], bool(os.environ.get("ADO_CONFIG")), cfg.strict["test_timeout"] if cfg else None))
    else:
        cmd = plan["test_command"]
        print(json.dumps({"applied": False, "status": plan["status"], "version": plan["version"], "counts": plan["counts"], "unverified": plan["unverified"],
                          "test_command": {"set": cmd is not None, "length": len(cmd) if cmd is not None else 0},
                          "test_timeout": _timeout_info(cfg, cmd),
                          "note": "read the profile and approve it in your own terminal window (stdin and stdout both a terminal): `ado stack approve`, then `--apply`"}, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ado", description="Azure DevOps work-item CLI")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="Configure this repo/team and verify access (read-only)")
    p.add_argument("--profile", choices=("starter", "team", "strict"), default=None,
                   help="also write that profile's config keys (an existing config shows `key: old -> new` and asks y/N)")
    p = sub.add_parser("doctor", help="What is wrong with this setup: git, Spec Kit, config, token, identity, extension, workflow, approvers, "
                       "stack and kits, as ok/warn/fail/unknown rows with a fix (read-only; exit 1 when a row fails)")
    p.add_argument("--json", action="store_true", help='print {"rows": [{"id", "status", "fix"}], "scopes": {}} instead of text')
    p.add_argument("--probe-item", type=int, metavar="ID", help="a work item to probe write access against; accepted now, used by the scope probes (not in this build)")
    p = sub.add_parser("status", help="Which stage a story is at and the one command that moves it on (read-only; board stages need the network)")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--offline", action="store_true", help="do not read ADO: the board stages are 'not checked', never met")
    p.add_argument("--catch-up", action="store_true", help="also print the ordered commands that bring the story from its first unmet stage to an open PR (after an aborted run)")
    p = sub.add_parser("calibrate", help="How far a model's plane 8 (AC fidelity) reading can be trusted: --emit writes the rubric and 12 unlabelled "
                       "golden items under .ado/; --score reads the model's JSON answer (exit 0 at 10 of 12 and no false pass, else 1; local only)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--emit", metavar="PATH", help="write the calibration prompt to PATH (a .md file under .ado/)")
    g.add_argument("--score", metavar="FILE", help='score the answer in FILE (under .ado/, at most 64 KiB): {"G1": "asserts|partial|does not assert", ...}')
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
    p.add_argument("--author-ids", action="store_true",
                   help="print `date | author id | first 40 scrubbed characters` per comment (to find an approver's identity id); writes nothing")
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
    p.add_argument("--sync", action="store_true", help="record the approvers' `/answer Q<n>: text` comments of the story as the PO's answers in the "
                   "ledger and list the questions due under unattended.assume_after_hours (a local write, no ADO write; needs approvers on the target branch)")
    p.add_argument("--if-comment-mode", action="store_true", help="with --sync: exit 0 with a note, before any token or request, unless `approval` is "
                   "\"comment\" on the target branch (the workflow's sync step uses it, so a terminal-mode run is unchanged)")
    p = sub.add_parser("rules", help="Pull the business rules of the story's Epic/Feature ancestors into <dod_path>/Rules_<id>.md "
                       "(reads ADO with GET only)")
    p.add_argument("id", type=int)
    p.add_argument("--confirm", action="store_true", help="local only: record in the ledger header the hashes of the rules in this story's scope "
                   "(`Rules reviewed:`). New ids need no terminal; re-confirming a changed hash or dropping a recorded id needs your own terminal and a typed code")
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
    p.add_argument("--sync", action="store_true", help="record the approvers' `/approve D<n>` and `/reject D<n>` comments of the story as verdicts in "
                   "the decisions file (a local write, no ADO write; needs approvers on the target branch; with them, --resolve is refused)")
    p.add_argument("--if-comment-mode", action="store_true", help="with --sync: exit 0 with a note, before any token or request, unless `approval` is "
                   "\"comment\" on the target branch; then also when the decisions file does not exist at all (any other refusal stays exit 2). "
                   "The workflow's sync step uses it, so a terminal-mode run is unchanged")
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
    p.add_argument("--base", metavar="REF", help="CI: the pre-merge target branch (a full commit id or a ref) the approvers are read from, never the merged "
                   "commit's own config; default origin/HEAD")

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

    p = sub.add_parser("kits", help="Spec Kit extensions against the lockfile ado.kits.lock.json (local, read-only)")
    ksub = p.add_subparsers(dest="kits_command", required=True)
    ksub.add_parser("verify", help="List the installed kits and whether each is pinned, changed or unpinned (JSON); exit 2 on any kit "
                    "that is unpinned or changed since a human pinned it, or when the registry or a kit file cannot be trusted")
    q = ksub.add_parser("pin", help="A human pins one installed kit in ado.kits.lock.json (a terminal is needed for --apply): records digests of the kit "
                        "files and agent copies, the scan facts and who reviewed it. Preview by default: digests, scan counts with file:line and a "
                        "fixed 5-line checklist, never kit text. A --dev install, a symlink and an empty kit dir cannot be pinned. A human approval "
                        "record, process enforcement and not a sandbox")
    q.add_argument("id", help="an installed kit id (a-z, 0-9, -)")
    q.add_argument("--by", default="tech lead", metavar="ROLE", help="a role, never a name (letters, digits, spaces; at most 40; default: tech lead)")
    q.add_argument("--apply", action="store_true", help="write the lockfile entry (default: preview, nothing written; both stdin and stdout must be terminals)")

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

    q = stsub.add_parser("init", help="Create the DRAFT profile ado.stack.json from a seed (package data) or from a file the agent wrote under "
                         ".ado/. Local write, no ADO call, no gate: the draft is not trusted until approved. Preview by default (counts and "
                         "error positions only, never profile text); --apply writes; an approved profile needs --force")
    src = q.add_mutually_exclusive_group(required=True)
    src.add_argument("--from", dest="from_file", metavar="FILE", help="a regular file inside .ado/ (validated; status forced to draft, approval dropped)")
    src.add_argument("--seed", metavar="NAME", action="append", help="one of: " + ", ".join(stack.SEEDS) + ". Repeat it for a repository with "
                     "several stacks: one draft with one stack per seed (each at its detected root)")
    q.add_argument("--root", metavar="R", help="with ONE --seed: the stack's root (default: the one detected root that fits, else .)")
    q.add_argument("--stack", metavar="ID", help="name the stack (a profile with exactly one stack)")
    q.add_argument("--apply", action="store_true", help="write ado.stack.json (default: preview, nothing written)")
    q.add_argument("--force", action="store_true", help="with --apply: replace an approved profile")

    q = stsub.add_parser("approve", help="A human approves the profile (a terminal is needed for --apply): status approved, version + 1, an approval "
                         "record, and strict.test_command in ado.config.json set to the composed command (or removed). Preview by default: in a "
                         "terminal every command is printed in full, otherwise counts and the command's length only. Process enforcement, "
                         "not a security boundary; the approval is a record, not proof")
    q.add_argument("--story", metavar="ID", help="also record one decision line `Stack profile v<n> approved, sha256 <12>` for this story (digits)")
    q.add_argument("--by", default="tech lead", metavar="ROLE", help="a role, never a name (letters, digits, spaces; at most 40; default: tech lead)")
    q.add_argument("--apply", action="store_true", help="write the approval and the config (default: preview, nothing written)")

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

    p = sub.add_parser("approvals", help="Comment approval (approval: \"comment\" on the target branch): check a workflow gate")
    asub = p.add_subparsers(dest="approvals_command", required=True)
    q = asub.add_parser("check", help="Exit 0 when the gate is met, 2 with a fixed text when not (read-only). Review gates need an approver's "
                        "`/approve <gate> <hash>`; wait-po-answers and wait-po-validation are met by the approvers' /answer, /approve and /reject comments; "
                        "a no-op when approval is not \"comment\"")
    q.add_argument("id", type=int, help="story id")
    q.add_argument("--gate", required=True, metavar="GATE", help="one of: " + ", ".join(approvals.GATES))
    q = asub.add_parser("sync", help="The poller (comment mode only, for a local or persistent runner): reads the gate a Spec Kit run is paused at, prints "
                        "{gate, verdict: approve|reject|waiting} from the approvers' comments, and with --resume resumes the run with that verdict. When "
                        "`unattended.approval_requests` is true on the target branch it also posts one approval-request comment per hash (the one write "
                        "without a preview and a gate). Exit 0, 1 (the resume failed), 2 (refused)")
    q.add_argument("run_id", metavar="RUN_ID", help="the Spec Kit run id")
    q.add_argument("--resume", action="store_true", help="run `specify workflow resume` with the verdict when there is one")
    q.add_argument("--dry-run", action="store_true", help="post nothing and resume nothing: only print what would happen")

    args = parser.parse_args(argv)
    if getattr(args, "expect_plan", None) and not args.apply:
        parser.error("--expect-plan needs --apply")
    if args.command in ("questions", "decisions") and args.apply and not args.post:
        parser.error("--apply needs --post")
    if args.command in ("questions", "decisions") and args.if_comment_mode and not args.sync:
        parser.error("--if-comment-mode needs --sync")
    if args.command == "questions" and args.sync and (args.post or args.apply or args.plan_out or args.expect_plan):
        parser.error("--sync cannot be combined with --post, --apply, --plan-out or --expect-plan")
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
        if args.sync and (args.post or args.apply or args.plan_out or args.expect_plan or args.if_any or args.add is not None or args.resolve or args.verdict
                          or args.by is not None or args.note is not None or args.force or args.kind is not None or args.why is not None
                          or args.supersedes is not None):
            parser.error("--sync cannot be combined with any other decisions option")
        if args.resolve and (args.post or args.apply or args.plan_out or args.expect_plan):
            parser.error("--resolve cannot be combined with --post, --apply, --plan-out or --expect-plan")
        if args.resolve and not args.verdict:
            parser.error("--resolve needs --validated or --rejected")
        if not args.resolve and (args.verdict or args.by is not None or args.note is not None or args.force):
            parser.error("--validated, --rejected, --by, --note and --force need --resolve")
    if args.command == "comments" and args.author_ids and args.out is not None:
        parser.error("--author-ids writes nothing and takes no --out")
    if getattr(args, "dir", None) and args.command == "attachments" and not args.download:
        parser.error("--dir needs --download")
    if args.command == "stack" and args.stack_command == "init":
        if args.root is not None and (args.seed is None or len(args.seed) > 1):
            parser.error("--root needs exactly one --seed")
        if args.force and not args.apply:
            parser.error("--force needs --apply")
    if (args.command == "stack" and args.stack_command == "approve") or (args.command == "kits" and args.kits_command == "pin"):
        if not stack._ROLE.fullmatch(args.by):
            parser.error(stack.E_ROLE)
        if getattr(args, "story", None) is not None and not stack.valid_story(args.story):
            parser.error("--story must be a work item id (digits only, no leading zero)")
    if args.command == "doctor" and args.probe_item is not None and not 1 <= args.probe_item <= prs.MAX_ID:
        parser.error(f"--probe-item must be a work item id between 1 and {prs.MAX_ID}")
    if args.command == "status" and not 1 <= args.id <= prs.MAX_ID:
        parser.error(f"the story id must be between 1 and {prs.MAX_ID}")
    if args.command == "approvals" and args.approvals_command == "check" and not 1 <= args.id <= prs.MAX_ID:
        parser.error(f"the story id must be between 1 and {prs.MAX_ID}")
    if args.command == "rules" and not 1 <= args.id <= 999999999:
        parser.error("the story id must be between 1 and 999999999")
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
            return run_setup(profile=args.profile)
        if args.command == "doctor":  # rows, not an exception: every failure is a row
            return _doctor(args)
        if args.command == "calibrate":  # local only: no token, no request, no config
            return _calibrate(args)
        if args.command == "status":  # a report: every failure is a stage, not an exception
            return _status(args)
        if args.command == "decisions" and args.add is not None:  # local only: no token, no request
            out = _add_decision(args)
        elif args.command == "decisions" and args.resolve:  # local only: no token, no request
            out = _resolve_decision(args)
        elif args.command == "rules" and args.confirm:  # local only: no token, no request
            out = _rules_confirm(args)
        elif args.command in ("questions", "decisions") and args.sync and args.if_comment_mode and not _comment_mode(trusted(".")):
            out = {"id": args.id, "synced": False, "note": E_NOT_COMMENT_MODE}  # terminal mode: no token, no client, no file read
        elif (args.command == "decisions" and args.sync and args.if_comment_mode
              and not os.path.lexists(decisions.decisions_file(_config(args).dod_path, args.id))):  # a link, even a dangling one, is a file that cannot be read
            out = {"id": args.id, "synced": False, "note": E_NO_DECISIONS_FILE}
        elif args.command == "approvals":  # `check` reads only (terminal mode needs no token); `sync` is the poller
            out = _approvals_check(args) if args.approvals_command == "check" else _approvals_sync(args)
        elif args.command == "comments" and args.author_ids:  # reads only; stdout lines, no file
            lines, truncated = comments.author_id_lines(AdoClient(_config(args)), args.id)
            print("\n".join(lines), end="\n" if lines else "")
            if truncated:
                print("note: not every comment was read (page limit or repeated page token)", file=sys.stderr)
            return 0
        elif args.command == "kits":  # local only: no token, no request, no config
            return _kits_pin(args) if args.kits_command == "pin" else _kits_verify(args)
        elif args.command == "stack" and args.stack_command == "show":  # local only: no token, no request
            return _stack_show(args)
        elif args.command == "stack" and args.stack_command == "init":  # local write: no token, no request, no gate
            return _stack_init(args)
        elif args.command == "stack" and args.stack_command == "approve":  # local write: no token, no request, no gate; a terminal for --apply
            return _stack_approve(args)
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
                if strict.edits_trust_files(client.config, args.src):  # the PR edits the profile, lockfile or test command: say so in the comment
                    out["trust_note"] = gate.TRUST_NOTE
                if note := strict.ignored_roots_note(args.src):  # roots no profile can name are outside the stack rules: say so, never block
                    out["ignored_roots_note"] = note
                if strict.adds_kits_lock(client.config, args.src):  # the first kits lockfile: nobody has reviewed its pins as code
                    out["kits_trust_note"] = gate.KITS_TRUST_NOTE
            elif args.command == "gate" and args.base is not None and client.config.mode == "strict":  # a strict PR config under --base
                out["config"] = gate.PR_CONFIG_NOTE
                stack_note, kits_note = strict.adoption_notes(client.config, args.src, args.base)  # the target is not strict: this PR turns it on
                if stack_note:
                    out["trust_note"] = gate.TRUST_NOTE
                if kits_note:
                    out["kits_trust_note"] = gate.KITS_TRUST_NOTE
    except comments.CommentsUnreadable as e:  # `--author-ids`: a fixed reason key, exit 1, no traceback
        err, code = e, 1
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
