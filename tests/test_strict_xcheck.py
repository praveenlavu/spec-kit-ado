# P4-11: the done-time cross-check `decisions-xcheck-*` (D§4.4, D§7 AC 9). With approvers configured, `ado done` (preview and apply) and the CI close step
# refuse a validated/rejected line that no approver comment backs, so an agent cannot hand-write a `validated` line.
import copy
import json

import pytest

from ado import __main__ as m
from ado import decisions, strict
from ado.client import AdoError
from ado.config import Config, ConfigInvalid, Trust
from tests.conftest import FakeClient, comment_pages, connection_data, versions_response
from tests.test_decisions import APPR, KITID, S1, S2, S3, T_POST, T_V1, T_V2, T_V3, TOK, _file, _kit, _posted, _rows, _sync, _vote

OTHER = "66666666-6666-4666-8666-666666666666"
SEKRET = "SEKRET-sentinel"
RULES = {r: f"decisions-xcheck-{r}" for r in ("marker", "dropped", "verdict", "truncated", "unreachable")}


def _x(cfg, md, raws, approvers=(APPR,), excluded=(TOK, KITID), responses=(), truncated=False):
    client = FakeClient(cfg, responses=list(responses))
    return strict.xcheck(md, _rows(cfg, raws), truncated, list(approvers), set(excluded), client, 1001)


def _rules(findings):
    return [f["rule"] for f in findings]


def _clean(cfg):
    """(a decisions file whose D1 and D2 `--sync` resolved, its comments): the clean story."""
    md, html = _posted(S1, S2, S3)
    raws = [_kit(html), _vote(11, "/approve D1"), _vote(12, "/reject D2: no", T_V2)]
    (new, _), _ = _sync(cfg, md, raws)
    return new, raws


def test_clean_story_passes(cfg):
    new, raws = _clean(cfg)
    assert _x(cfg, new, raws) == []
    assert _x(cfg, _clean(cfg)[0].replace("D1", "D1"), raws) == []  # idempotent
    md, html = _posted(S1)  # posted and still open: nothing to cross-check yet
    assert _x(cfg, md, [_kit(html)]) == []
    assert _x(cfg, "", []) == []  # no decisions at all


def test_a_later_reject_and_a_reposted_line_stay_clean(cfg):
    md, html = _posted(S1)
    raws = [_kit(html), _vote(11, "/approve D1"), _vote(13, "/reject D1: changed my mind", T_V3)]
    (new, _), _ = _sync(cfg, md, raws)
    assert "rejected" in new and _x(cfg, new, raws) == []
    # a re-posted (edited) line: the marker follows the newest posting and the verdict is newer than it
    edited, html2 = _posted(S1.replace("cursor paging", "offset paging"), cid=20)
    raws = [_kit(html), _kit(html2, cid=20, date=T_V1), _vote(21, "/approve D1", T_V2)]
    (new, _), _ = _sync(cfg, edited, raws)
    assert _x(cfg, new, raws) == []


def test_forged_marker_and_another_storys_marker(cfg):
    md, html = _posted(S1)
    h = decisions.line_hash("Use cursor paging")
    for forged in (f"posted: 99:{h}", f"posted: 10:{'0' * 8}"):  # names no comment of this story / a hash that is not the line's
        bad = md.replace(f"posted: 10:{h}", forged)
        assert bad != md
        assert _rules(_x(cfg, bad, [_kit(html)])) == [RULES["marker"]], forged
    # the named comment exists but is another thing: a human's, or an unmarked one
    assert _rules(_x(cfg, md, [_kit(html, author=OTHER)], excluded=(TOK,))) == [RULES["marker"]]
    assert _rules(_x(cfg, md, [_kit("<p>looks fine</p>")])) == [RULES["marker"]]


def test_edited_line(cfg):
    md, html = _posted(S1, S2)
    edited = md.replace("Use cursor paging", "Use offset paging")  # marker hash no longer matches
    assert _rules(_x(cfg, edited, [_kit(html)])) == [RULES["marker"]]
    h = decisions.line_hash("Use offset paging")  # re-hashed after the edit: the posting does not hold it
    rehashed = edited.replace(decisions.line_hash("Use cursor paging"), h)
    assert _rules(_x(cfg, rehashed, [_kit(html)])) == [RULES["marker"]]


def test_deleted_line_and_deleted_file(cfg):
    md, html = _posted(S1, S2)
    gone = "\n".join(ln for ln in md.splitlines() if not ln.startswith("- [ ] D2")) + "\n"
    out = _x(cfg, gone, [_kit(html)])
    assert _rules(out) == [RULES["dropped"]] and "D2" in out[0]["detail"] and "D1" not in out[0]["detail"]
    assert _rules(_x(cfg, "", [_kit(html)])) == [RULES["dropped"]]  # the whole file removed


def test_hand_written_verdict_with_or_without_via(cfg):
    md, html = _posted(S1, S2)
    for hand in (decisions.resolve(md, "D1", "validated", "PO", "agreed"),                       # a terminal verdict
                 decisions.resolve(md, "D1", "validated", "approver", "", False, "777"),        # a `via` that names no comment
                 decisions.resolve(md, "D1", "validated", "approver", "", False, "10")):         # a `via` that names the kit's own posting
        assert _rules(_x(cfg, hand, [_kit(html)])) == [RULES["verdict"]], hand
    # unposted, never shown to the PO: a verdict cannot be an approver's
    unposted = _file(S1)
    assert _rules(_x(cfg, decisions.resolve(unposted, "D1", "validated", "PO", ""), [])) == [RULES["verdict"]]


def test_via_pointing_at_the_agents_own_approve(cfg):
    md, html = _posted(S1)
    fake = decisions.resolve(md, "D1", "validated", "approver", "", False, "11")
    for author in (TOK, KITID, OTHER):  # the token identity, a kit identity, somebody who is no approver
        assert _rules(_x(cfg, fake, [_kit(html), _vote(11, "/approve D1", T_V2, author=author)])) == [RULES["verdict"]], author
    assert _rules(_x(cfg, fake, [_kit(html), _vote(11, "/approve D1", T_V2, on_behalf=APPR)])) == [RULES["verdict"]]  # a forged author
    assert _rules(_x(cfg, fake, [_kit(html), _vote(11, "<blockquote>/approve D1</blockquote>", T_V2)])) == [RULES["verdict"]]  # quoted
    assert _x(cfg, fake, [_kit(html), _vote(11, "/approve D1", T_V2)]) == []  # the same line behind a real approver comment passes


def test_verdict_older_than_its_posting(cfg):
    md, html = _posted(S1)
    fake = decisions.resolve(md, "D1", "validated", "approver", "", False, "8")
    assert _rules(_x(cfg, fake, [_kit(html), _vote(8, "/approve D1", "2026-10-01T09:00:00Z")])) == [RULES["verdict"]]
    assert _rules(_x(cfg, fake, [_kit(html), _vote(8, "/approve D1", T_POST)])) == [RULES["verdict"]]  # same instant: the id breaks the tie (8 < 10)


def test_verdict_must_be_the_newest_valid_command_with_the_matching_verb(cfg):
    new, raws = _clean(cfg)
    assert _x(cfg, new, raws) == []
    assert _rules(_x(cfg, new, raws + [_vote(13, "/reject D1", T_V3)])) == [RULES["verdict"]]       # a newer reject: the line is stale
    assert _rules(_x(cfg, new.replace("D1: Use cursor paging → validated", "D1: Use cursor paging → rejected"), raws)) == [RULES["verdict"]]  # the other verb
    assert _x(cfg, new, raws + [_vote(13, "/reject D1", T_V3, author=TOK)]) == []                    # the agent's later reject does not count
    assert _rules(_x(cfg, new, raws + [_vote(13, "/reject D1 oops", T_V3)])) == [RULES["verdict"]]   # a retraction typed wrong cannot be told from one
    assert _rules(_x(cfg, new, raws, approvers=(OTHER,))) == [RULES["verdict"]]                      # not an approver on the target branch


def test_by_and_note_are_what_sync_wrote(cfg):
    new, raws = _clean(cfg)
    assert _rules(_x(cfg, new.replace("by: approver", "by: PO"), raws)) == [RULES["verdict"]]
    assert _rules(_x(cfg, new.replace("→ rejected · by: approver · note: no", "→ rejected · by: approver · note: a different reason"), raws)) == [RULES["verdict"]]


def test_a_via_taken_off_a_synced_line_is_dropped_and_unbacked(cfg):
    new, raws = _clean(cfg)
    stripped = new.replace(" · via: 11", "")
    assert stripped != new
    out = _x(cfg, stripped, raws)
    assert _rules(out) == [RULES["dropped"], RULES["verdict"]] and "D1" in out[0]["detail"] and "D2" not in out[0]["detail"]


def test_a_line_set_back_to_an_older_posting_is_dropped(cfg):
    old, html = _posted(S1, cid=10)
    new_md, html2 = _posted(S1.replace("cursor paging", "offset paging"), cid=20)
    raws = [_kit(html), _kit(html2, cid=20, date=T_V1)]
    assert _x(cfg, new_md, raws) == []
    assert _rules(_x(cfg, old, raws)) == [RULES["dropped"]]  # the old line with its old (valid) marker: the PO was shown newer words


def test_posting_comment_edited_lossy_or_markup(cfg):
    md, html = _posted(S1)
    edited = _kit(html, version=2, modified_by=KITID)
    versions = [versions_response((1, "<p>old</p>", T_POST), (2, html, T_V1))]
    assert _rules(_x(cfg, md, [edited], responses=versions)) == [RULES["marker"]]
    assert _rules(_x(cfg, md, [_kit(f"<div>{html}</div>")])) == [RULES["marker"]]
    lossy = _file("- [ ] D1: Mail dana@contoso.example → _(open)_ · kind: decision · why: w")
    plan = decisions.plan_post(None, 1001, decisions.parse_decisions(lossy))
    lossy = decisions._marked(lossy, plan["hashes"], 10)
    assert _rules(_x(cfg, lossy, [_kit(plan["html"])])) == [RULES["marker"]]


def test_an_unreadable_or_edited_decisions_comment_no_line_points_at_is_a_marker_finding(cfg):
    md, html = _posted(S1)
    extra = _kit(f"<span>{html}</span>", cid=30, date=T_V1)  # a decisions comment whose lines the PO could not all see
    assert _rules(_x(cfg, md, [_kit(html), extra])) == [RULES["marker"]]


def test_truncated_list(cfg):
    new, raws = _clean(cfg)
    out = _x(cfg, new, raws, truncated=True)
    assert _rules(out) == [RULES["truncated"]]


def test_network_failure_is_a_finding_not_a_pass(cfg):
    new, raws = _clean(cfg)
    boom = AdoError(f"HTTP 500: {SEKRET}", status=500)
    # an edited verdict comment needs its versions list: the request fails
    fake = new
    out = _x(cfg, fake, raws[:2] + [_vote(12, "/reject D2: no", T_V2, version=2)], responses=[boom, boom, boom])
    assert _rules(out) == [RULES["unreachable"]] and SEKRET not in json.dumps(out)


def test_findings_hold_ids_and_fixed_texts_only(cfg):
    secret = "ZEBRA-sentinel"
    md, html = _posted(S1.replace("Use cursor paging", secret))
    bad = decisions.resolve(md, "D1", "validated", "PO", f"note {secret}")
    out = _x(cfg, bad, [_kit(html + f"<br>{secret}")])
    assert out and secret not in json.dumps(out)
    for f in out:
        assert set(f) == {"rule", "detail", "fix"} and f["rule"] in RULES.values()


def test_a_file_that_cannot_be_read_line_by_line_is_dropped(cfg):
    md, html = _posted(S1)
    for bad in (md + S1 + "\n", md + "- [ ] D2: broken line\n", md + "<!-- never closed\n", md + "- [ ] D٣: x → _(open)_ · kind: decision · why: w\n"):
        assert _rules(_x(cfg, bad, [_kit(html)])) == [RULES["dropped"]], bad


# ---- xcheck_run: the fetch (bypass bit, comments once, excluded) ----
GUID = "99999999-9999-4999-8999-999999999999"


class Route(FakeClient):
    """A client that answers by path: the project, the bypass bit, the comments (one page), the token identity; the rest {}."""

    def __init__(self, config, raws=(), bypass=False, me=TOK, fail=None, pages=None):
        super().__init__(config)
        self.raws, self.bypass, self.me, self.fail, self.pages = list(raws), bypass, me, fail, pages

    def request(self, method, path, body=None, content_type="application/json", raw=False):
        self.calls.append({"method": method, "path": path, "body": body, "content_type": content_type})
        if self.fail and self.fail in path:
            raise AdoError(f"HTTP 500: {SEKRET}", status=500)
        if "/_apis/permissions/" in path:
            return {"count": 1, "value": [self.bypass]}
        if "/_apis/projects/" in path:
            return {"id": GUID, "name": "Fabrikam"}
        if "connectionData" in path:
            return connection_data(self.me)
        if "/comments?" in path:
            return {"comments": copy.deepcopy(self.raws)}
        return {"url": "https://example.invalid/a/1"} if method != "GET" else {}


def test_xcheck_run_clean_and_fixed_findings(cfg):
    new, raws = _clean(cfg)
    assert strict.xcheck_run(Route(cfg, raws), 1001, new, [APPR]) == []
    for kw in ({"bypass": True}, {"bypass": None}, {"fail": "/comments?"}, {"fail": "connectionData"}, {"fail": "/_apis/permissions/"}):
        out = strict.xcheck_run(Route(cfg, raws, **kw), 1001, new, [APPR])
        assert _rules(out) == [RULES["unreachable"]] and SEKRET not in json.dumps(out), kw
    # the agent runs with a PAT that can bypass rules: comments cannot be told apart from forged ones
    assert strict.xcheck_run(Route(cfg, raws, me="not a guid"), 1001, new, [APPR])[0]["rule"] == RULES["unreachable"]
    assert strict.xcheck_run(Route(cfg, [raws[0], {"id": 5, "text": 1}]), 1001, new, [APPR])[0]["rule"] == RULES["unreachable"]  # CommentsUnreadable


def test_xcheck_run_truncated_and_ci_identity(cfg):
    new, raws = _clean(cfg)

    class Looping(Route):
        def request(self, method, path, *a, **k):
            if "/comments?" in path:
                return {"comments": [], "continuationToken": "same"}
            return super().request(method, path, *a, **k)
    assert _rules(strict.xcheck_run(Looping(cfg, raws), 1001, new, [APPR])) == [RULES["truncated"]]
    # the CI identity is excluded as well: its own `/approve` backs nothing
    ci = "77777777-7777-4777-8777-777777777777"
    fake = decisions.resolve(_posted(S1)[0], "D1", "validated", "approver", "", False, "11")
    posting = _posted(S1)[1]
    rows = [_kit(posting), _vote(11, "/approve D1", T_V2, author=ci)]
    assert strict.xcheck_run(Route(cfg, rows), 1001, fake, [ci]) == []  # a client that is not the CI identity does not know it (in CI the client is it: `me`)
    assert _rules(strict.xcheck_run(Route(cfg, rows), 1001, fake, [ci], ci_identity=ci)) == [RULES["verdict"]]
    assert _rules(strict.xcheck_run(Route(cfg, rows, me=ci), 1001, fake, [ci])) == [RULES["verdict"]]
    assert _rules(strict.xcheck_run(Route(cfg, rows), 1001, fake, [ci], ci_identity="nope")) == [RULES["unreachable"]]


# ---- ado done ----
def _dec_env(tmp_path, monkeypatch, md, raws, approvers=(APPR,), mode="advisory", base_seen=None, **route):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", "mode": mode}))
    (tmp_path / "dod").mkdir(exist_ok=True)
    (tmp_path / "dod" / "Decisions_1001.md").write_bytes(md.encode("utf-8"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)

    def trust(repo=".", base=None):
        if base_seen is not None:
            base_seen.append(base)
        if isinstance(approvers, BaseException):
            raise approvers
        return Trust(list(approvers), "comment" if approvers else "terminal", {})
    monkeypatch.setattr(m, "trusted", trust)
    seen = {}

    def make(c):
        seen["c"] = Route(c, raws, **route)
        return seen["c"]
    monkeypatch.setattr(m, "AdoClient", make)
    return seen


def _ledger(tmp_path):
    from tests.test_done import ledger
    (tmp_path / "dod" / "DoD_1001.md").write_text(ledger("- [x] a"), encoding="utf-8")


def _forged(cfg):
    md, html = _posted(S1)
    return decisions.resolve(md, "D1", "validated", "approver", "", False, "777"), [_kit(html)]


def test_advisory_warns_only(tmp_path, monkeypatch, capsys, cfg):
    md, raws = _forged(cfg)
    seen = _dec_env(tmp_path, monkeypatch, md, raws)
    _ledger(tmp_path)
    assert m.main(["done", "1001", "--plan-out", "out/plan.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ready"] is True and len(out["warnings"]) == 1 and out["warnings"][0].startswith("decisions-xcheck-verdict: ")
    assert "D1" in out["warnings"][0]
    assert not any(c["method"] != "GET" for c in seen["c"].calls)
    # --apply is not stopped in advisory mode: it still attaches and closes
    seen = _dec_env(tmp_path, monkeypatch, md, raws)
    assert m.main(["done", "1001", "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is True and any(c["method"] == "PATCH" for c in seen["c"].calls)


def test_strict_blocks_preview_and_apply_and_sends_nothing(tmp_path, monkeypatch, capsys, git_env):
    from ado.config import Config
    from tests.test_strict import DEC
    from tests.test_strict_wire import _tick, _wire
    cfg = Config(organization="contoso", project="Fabrikam")
    md, raws = _forged(cfg)
    mod, fake, sent, s = _wire(tmp_path, monkeypatch, git_env, dec=md)
    _tick(tmp_path)
    monkeypatch.setattr(m, "trusted", lambda repo=".", base=None: Trust([APPR], "comment", {}))
    routes = []
    monkeypatch.setattr(m, "AdoClient", lambda c: routes.append(Route(c, raws)) or routes[-1])
    before = DEC(tmp_path).read_bytes()
    for extra in ([], ["--apply"]):
        assert m.main(["done", "1001", "--plan-out", "out/plan.md", *extra]) == 2
        cap = capsys.readouterr()
        out = json.loads(cap.out)
        assert out["ok"] is False and _rules(out["findings"]) == [RULES["verdict"]]
        assert cap.err.startswith("error: strict check failed: decisions-xcheck-verdict (") and SEKRET not in cap.err + cap.out
        assert not (tmp_path / "out").exists() and not any(c["method"] != "GET" for c in routes[-1].calls)
    assert DEC(tmp_path).read_bytes() == before  # nothing stamped
    # the same story with the verdict behind a real approver comment closes
    ok = decisions.resolve(_posted(S1)[0], "D1", "validated", "approver", "", False, "11")
    DEC(tmp_path).write_bytes(ok.encode())
    routes.clear()
    monkeypatch.setattr(m, "AdoClient", lambda c: routes.append(Route(c, [_kit(_posted(S1)[1]), _vote(11, "/approve D1", T_V2)])) or routes[-1])
    assert m.main(["done", "1001", "--plan-out", "out/plan.md"]) == 0
    assert "warnings" not in json.loads(capsys.readouterr().out)


def test_strict_done_unreachable_fails_closed(tmp_path, monkeypatch, capsys, git_env):
    from ado.config import Config
    from tests.test_strict_wire import _tick, _wire
    cfg = Config(organization="contoso", project="Fabrikam")
    md, raws = _forged(cfg)
    _wire(tmp_path, monkeypatch, git_env, dec=md)
    _tick(tmp_path)
    monkeypatch.setattr(m, "trusted", lambda repo=".", base=None: Trust([APPR], "comment", {}))
    monkeypatch.setattr(m, "AdoClient", lambda c: Route(c, raws, fail="/comments?"))
    assert m.main(["done", "1001"]) == 2
    cap = capsys.readouterr()
    assert _rules(json.loads(cap.out)["findings"]) == [RULES["unreachable"]] and SEKRET not in cap.out + cap.err


def test_not_run_without_approvers(tmp_path, monkeypatch, capsys, cfg):
    """No approvers on the target branch: no request for the cross-check, and the output is exactly the plain `done` output."""
    md, raws = _forged(cfg)
    monkeypatch.setattr(strict, "xcheck_run", lambda *a, **k: pytest.fail("the cross-check ran without approvers"))
    seen = _dec_env(tmp_path, monkeypatch, md, raws, approvers=())
    _ledger(tmp_path)
    assert m.main(["done", "1001", "--plan-out", "out/plan.md"]) == 0
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out == {"id": 1001, "applied": False, "ready": True, "unticked": [], "decisions_file": "dod/Decisions_1001.md"}
    assert cap.err == "" and [c["path"] for c in seen["c"].calls if any(k in c["path"] for k in ("comments", "connectionData", "permissions"))] == []


def test_done_reads_approvers_from_the_given_base_and_refuses_what_it_cannot_trust(tmp_path, monkeypatch, capsys, cfg):
    md, raws = _forged(cfg)
    base = "0123456789abcdef0123456789abcdef01234567"
    bases = []
    _dec_env(tmp_path, monkeypatch, md, raws, base_seen=bases)
    monkeypatch.setattr(m.Config, "load_from_ref", classmethod(lambda cls, ref, repo=".": None))  # the target has no config of its own
    _ledger(tmp_path)
    assert m.main(["done", "1001", "--base", base]) == 0 and bases == [base]
    capsys.readouterr()
    assert m.main(["done", "1001", "--base", ""]) == 2  # an empty base must not fall back to origin/HEAD, the merged branch
    assert "--base" in capsys.readouterr().err and bases == [base]
    _dec_env(tmp_path, monkeypatch, md, raws, approvers=ConfigInvalid("the target config is unreadable"))
    assert m.main(["done", "1001", "--apply"]) == 2  # an unreadable target config refuses; nothing is sent
    assert "unreadable" in capsys.readouterr().err


def test_a_pr_that_sets_advisory_cannot_turn_its_own_cross_check_into_a_warning(tmp_path, monkeypatch, capsys, cfg):
    """The close step runs on the merged commit, whose `mode` the PR wrote: a strict TARGET branch still blocks."""
    md, raws = _forged(cfg)
    base = "0123456789abcdef0123456789abcdef01234567"
    _dec_env(tmp_path, monkeypatch, md, raws, mode="advisory")
    _ledger(tmp_path)
    loaded = []
    monkeypatch.setattr(m.Config, "load_from_ref", classmethod(lambda cls, ref, repo=".": loaded.append(ref) or Config(organization="contoso", project="Fabrikam", mode="strict")))
    for extra in ([], ["--apply"]):
        assert m.main(["done", "1001", "--base", base, *extra]) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["mode"] == "strict" and _rules(out["findings"]) == [RULES["verdict"]]
    assert loaded == [base, base]


def test_a_posting_that_starts_with_an_invisible_character_is_still_a_posting(cfg):
    """A comment that has the decisions marker among its first 64 characters once whitespace and format characters are removed is an invalid
    posting (`-marker`), never ignored: otherwise an agent could post one and drop the rejected line it lists."""
    md, html = _posted(S1, S2)
    gone = "\n".join(ln for ln in md.splitlines() if not ln.startswith("- [ ] D2")) + "\n"
    assert _rules(_x(cfg, gone, [_kit(html)])) == [RULES["dropped"]]  # the plain posting is tracked: the dropped line shows
    for prefix in ("​", "﻿", "‎‏", " ⁠ ", "­"):
        hidden = _kit(prefix + html, cid=30, date=T_V1)
        out = _x(cfg, md, [_kit(html), hidden])
        assert _rules(out) == [RULES["marker"]], repr(prefix)
    # not a posting: the marker is far down the text, or it is another kit comment
    far = _kit("<p>" + "x" * 80 + "[spec-kit-ado:decisions]</p>", cid=31, date=T_V1)
    assert _x(cfg, md, [_kit(html), far]) == []
    assert _x(cfg, md, [_kit(html), _kit("[spec-kit-ado:questions]<br>Q1: x", cid=32, date=T_V1)]) == []


def test_marker_fix_text_names_a_non_approver_poster(cfg):
    md, html = _posted(S1)
    (f,) = _x(cfg, md.replace("posted: 10", "posted: 99"), [_kit(html)])
    assert "non-approver identity (the agent token" in f["fix"] and "a human re-posts" not in f["fix"]


# ---- F1 (P4-20 I-1): `done --base` is judged by the stricter of --base and origin/HEAD; it can never weaken the cross-check ----
def _done_repo(tmp_path, monkeypatch, git_env, head, old=None, mode="strict", extra=None, ci=False):
    """A real repository, `trusted` NOT stubbed: commit A (`old`, default no approvers), commit B = origin/main (`head`); local branches `old` (= A) and
    `side` (`extra`, when given). `ci`: no origin/HEAD, as a CI checkout. The working tree holds a forged `validated` D1 and a ticked ledger."""
    from tests.conftest import commit, git
    base = {"organization": "contoso", "project": "Fabrikam", "mode": mode}
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "a", {"ado.config.json": json.dumps(base) if old is None else old})
    a = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "branch", "old")
    commit(tmp_path, "b", {"ado.config.json": json.dumps({**base, "approvers": [APPR], "approval": "comment"}) if head is None else head, "b.txt": "b"})
    b = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    if not ci:
        git(tmp_path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    if extra is not None:
        git(tmp_path, "switch", "-q", "-c", "side")
        commit(tmp_path, "side", {"ado.config.json": extra, "side.txt": "s"})
        git(tmp_path, "switch", "-q", "main")
    (tmp_path / "dod").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    md, html = _posted(S1)
    (tmp_path / "dod" / "Decisions_1001.md").write_text(decisions.resolve(md, "D1", "validated", "approver", "", False, "777"), encoding="utf-8")
    _ledger(tmp_path)
    routes = []
    monkeypatch.setattr(m, "AdoClient", lambda c: routes.append(Route(c, [_kit(html)])) or routes[-1])
    monkeypatch.setattr(strict, "require", lambda r: {"overrides_used": []})  # the strict `done` check proper is not under test here
    monkeypatch.setattr(strict, "check", lambda *a, **k: {})
    return a, b, routes


def _cfg_json(*approvers, approval="comment", mode="strict"):
    d = {"organization": "contoso", "project": "Fabrikam", "mode": mode}
    return json.dumps({**d, "approvers": list(approvers), "approval": approval} if approvers else d)


@pytest.mark.parametrize("base", ["A", "old", None])
def test_done_base_old_sha_without_approvers_still_runs_xcheck(tmp_path, monkeypatch, capsys, git_env, base):
    a, _, routes = _done_repo(tmp_path, monkeypatch, git_env, None)
    argv = ["done", "1001", *(["--base", a if base == "A" else base] if base else [])]
    for extra in ([], ["--apply"]):
        assert m.main(argv + extra) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["mode"] == "strict" and _rules(out["findings"]) == [RULES["verdict"]]
        assert not any(c["method"] != "GET" for c in routes[-1].calls)


def test_done_base_old_sha_cannot_turn_the_check_into_a_warning_or_a_pass(tmp_path, monkeypatch, capsys, git_env):
    """Every side's mode counts: an ADVISORY working tree and an advisory old base cannot soften a strict origin/HEAD (blocking is not decided by the --base side alone)."""
    a, _, routes = _done_repo(tmp_path, monkeypatch, git_env, _cfg_json(APPR, mode="strict"), old=_cfg_json(mode="advisory"), mode="advisory")
    (tmp_path / "ado.config.json").write_text(_cfg_json(mode="advisory"))  # the merged config the PR wrote
    assert m.main(["done", "1001", "--base", a]) == 2
    assert _rules(json.loads(capsys.readouterr().out)["findings"]) == [RULES["verdict"]]


def test_done_base_advisory_both_sides_warns_only(tmp_path, monkeypatch, capsys, git_env):
    a, _, _ = _done_repo(tmp_path, monkeypatch, git_env, _cfg_json(APPR, mode="advisory"), old=_cfg_json(mode="advisory"), mode="advisory")
    assert m.main(["done", "1001", "--base", a]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["warnings"]) == 1 and out["warnings"][0].startswith("decisions-xcheck-verdict: ")


def test_done_base_stricter_of_different_approver_sets_refuses(tmp_path, monkeypatch, capsys, git_env):
    _, _, routes = _done_repo(tmp_path, monkeypatch, git_env, None, extra=_cfg_json(OTHER))
    for extra in ([], ["--apply"]):
        assert m.main(["done", "1001", "--base", "side", *extra]) == 2
        cap = capsys.readouterr()
        assert "different approvers" in cap.err and "a human closes the story" in cap.err and "comment approval" not in cap.err and OTHER not in cap.err + cap.out and APPR not in cap.err + cap.out
        assert not any(c["method"] != "GET" or "comments" in c["path"] for c in routes[-1].calls)


def test_done_base_same_approvers_on_both_sides_runs_xcheck(tmp_path, monkeypatch, capsys, git_env):
    _done_repo(tmp_path, monkeypatch, git_env, None, extra=_cfg_json(APPR))
    assert m.main(["done", "1001", "--base", "side"]) == 2
    assert _rules(json.loads(capsys.readouterr().out)["findings"]) == [RULES["verdict"]]


@pytest.mark.parametrize("head", [None, _cfg_json()])
def test_done_base_unresolvable_refuses(tmp_path, monkeypatch, capsys, git_env, head):
    """As before F1: a --base that is no commit here is exit 2 with a fixed text, whatever origin/HEAD says; no comment is read, nothing is sent."""
    _, _, routes = _done_repo(tmp_path, monkeypatch, git_env, head)
    for extra in ([], ["--apply"]):
        assert m.main(["done", "1001", "--base", "no/such-branch", *extra]) == 2
        assert "not a commit in this repository" in capsys.readouterr().err
    assert not any(c["method"] != "GET" or "comments" in c["path"] for c in routes[-1].calls)


@pytest.mark.parametrize("how", ["tag", "short-sha", "unfetched-name"])
def test_done_ci_unresolvable_base_with_the_approvers_removed_by_the_pr_refuses(tmp_path, monkeypatch, capsys, git_env, how):
    """No origin/HEAD (a CI checkout), the PR removed `approvers` from the working tree, and --base does not resolve to a commit (a tag is never used, a short
    sha is no full id, a branch that a shallow checkout did not fetch): exit 2, with and without --apply. Falling back to the working tree would drop the check."""
    from tests.conftest import git
    a, b, routes = _done_repo(tmp_path, monkeypatch, git_env, None, ci=True)
    (tmp_path / "ado.config.json").write_text(_cfg_json(mode="advisory"))
    git(tmp_path, "tag", "release", a)
    base = {"tag": "release", "short-sha": b[:7], "unfetched-name": "trunk"}[how]
    for extra in ([], ["--apply"]):
        assert m.main(["done", "1001", "--base", base, *extra]) == 2
        assert "not a commit in this repository" in capsys.readouterr().err
    assert not any(c["method"] != "GET" or "comments" in c["path"] for c in routes[-1].calls)


def test_done_terminal_mode_unchanged(tmp_path, monkeypatch, capsys, git_env):
    """No approvers anywhere: no comment request, and the output and exit codes do not depend on --base."""
    a, b, routes = _done_repo(tmp_path, monkeypatch, git_env, _cfg_json(), mode="advisory")
    outs = []
    for argv in (["done", "1001"], ["done", "1001", "--base", a], ["done", "1001", "--base", "old"]):
        assert m.main(argv) == 0
        cap = capsys.readouterr()
        assert cap.err == ""
        outs.append(cap.out)
    assert len(set(outs)) == 1 and json.loads(outs[0]) == {"id": 1001, "applied": False, "ready": True, "unticked": [], "decisions_file": "dod/Decisions_1001.md"}
    assert not any("comments" in c["path"] or "connectionData" in c["path"] for r in routes for c in r.calls)
    assert m.main(["done", "1001", "--base", "nope"]) == 2  # a base that is no commit here refused before F1 and still does


def test_done_terminal_mode_dangling_origin_head_exit_code_is_unchanged(tmp_path, monkeypatch, capsys, git_env):
    """origin/HEAD names a branch that does not exist: `done` already refused before F1 (it reads the target config); that stays, with and without --base."""
    from tests.conftest import git
    a, _, _ = _done_repo(tmp_path, monkeypatch, git_env, _cfg_json(), mode="advisory")
    git(tmp_path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    assert m.main(["done", "1001"]) == 2
    capsys.readouterr()
    assert m.main(["done", "1001", "--base", a]) == 2  # the other side cannot be read either: nothing is believed


def test_done_base_unreadable_config_refuses(tmp_path, monkeypatch, capsys, git_env):
    _, _, routes = _done_repo(tmp_path, monkeypatch, git_env, None, extra="{ not json")
    for argv in (["done", "1001", "--base", "side"], ["done", "1001", "--base", "side", "--apply"]):
        assert m.main(argv) == 2
        assert not any(c["method"] != "GET" for c in routes[-1].calls)
    capsys.readouterr()
    # an unreadable origin/HEAD config refuses even though the --base side is fine
    second = tmp_path / "second"
    second.mkdir()
    _done_repo(second, monkeypatch, git_env, "{ not json", old=_cfg_json(APPR))
    assert m.main(["done", "1001", "--base", "old"]) == 2


def test_done_ci_shape_without_origin_head_reads_the_pre_merge_base(tmp_path, monkeypatch, capsys, git_env):
    """CI: no origin/HEAD, `--base <full pre-merge sha>`. The target names approvers: the check runs, a forged line blocks, a real approval closes."""
    _, b, routes = _done_repo(tmp_path, monkeypatch, git_env, None, ci=True)
    assert m.main(["done", "1001", "--base", "0" * 40]) == 2  # a sha that is no commit here: fail closed, as before
    capsys.readouterr()
    full = b
    assert m.main(["done", "1001", "--base", full]) == 2
    assert _rules(json.loads(capsys.readouterr().out)["findings"]) == [RULES["verdict"]]
    ok = decisions.resolve(_posted(S1)[0], "D1", "validated", "approver", "", False, "11")
    (tmp_path / "dod" / "Decisions_1001.md").write_text(ok, encoding="utf-8")
    monkeypatch.setattr(m, "AdoClient", lambda c: Route(c, [_kit(_posted(S1)[1]), _vote(11, "/approve D1", T_V2)]))
    assert m.main(["done", "1001", "--base", full]) == 0


def test_done_ci_merge_result_as_origin_head_with_the_same_approvers_is_not_refused(tmp_path, monkeypatch, capsys, git_env):
    """CI with a fetched default branch (the merge result): same approvers on both sides => the real approval closes; changed approvers => human."""
    from tests.conftest import commit, git
    _done_repo(tmp_path, monkeypatch, git_env, None)
    full = git(tmp_path, "rev-parse", "refs/remotes/origin/main")
    ok = decisions.resolve(_posted(S1)[0], "D1", "validated", "approver", "", False, "11")
    (tmp_path / "dod" / "Decisions_1001.md").write_text(ok, encoding="utf-8")
    monkeypatch.setattr(m, "AdoClient", lambda c: Route(c, [_kit(_posted(S1)[1]), _vote(11, "/approve D1", T_V2)]))
    assert m.main(["done", "1001", "--base", full]) == 0
    commit(tmp_path, "pr changes approvers", {"ado.config.json": _cfg_json(OTHER)})
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert m.main(["done", "1001", "--base", full]) == 2  # refused: a human closes this one (the P4-11 backstop)
    assert "different approvers" in capsys.readouterr().err
