"""Role sign-offs (piece 5, S-2): `signoff-tpo|qa|bpo` gates checked by `ado approvals check --gate signoff-<role> [--offline]`. A sign-off is an
approver's `/approve signoff-<role> <hash>` comment where the hash covers the exact bytes signed; the signer must also hold the role in `roles.map`.
Fictional identities and data only."""
import json
import os

import pytest

from ado import __main__ as _main
from ado import approvals, signoff
from ado.client import AdoError
from ado.config import Config, Trust
from tests.conftest import RouteClient, ledger_md, raw_comment, versions_response
from tests.test_approvals import ANON, ME, T0, T1, T2, T3, KIT

QA_P, TPO_P, BPO_P, DEV_P, STRANGER = (f"{c}{c}{c}{c}{c}{c}{c}{c}-{c}{c}{c}{c}-4{c}{c}{c}-8{c}{c}{c}-{c}{c}{c}{c}{c}{c}{c}{c}{c}{c}{c}{c}" for c in "12345")
ROLES = {"map": {QA_P: ["qa"], TPO_P: ["tpo"], BPO_P: ["bpo", "qa"], DEV_P: ["dev"]}, "signoff": ["tpo", "qa", "bpo"]}
TRUST = Trust([QA_P, TPO_P, BPO_P, DEV_P], "comment", {}, ROLES)
QA_TEXT = "# QA for 1001\n\n## QA verification\n\n- [x] TC-1: totals -> pass · covers: AC-1 · how: manual\n"
STORY = 1001


def tree(root, spec="# Spec\nA\n", plan="# Plan\nB\n", tasks="- [ ] T001 first\n", qa=QA_TEXT, spec_dir="specs/001-x", extra=None):
    files = {"dod/DoD_1001.md": ledger_md(spec_dir=spec_dir), f"{spec_dir}/spec.md": spec, f"{spec_dir}/plan.md": plan, f"{spec_dir}/tasks.md": tasks,
             **({"dod/QA_1001.md": qa} if qa is not None else {}), **(extra or {})}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return Config(organization="contoso", project="Fabrikam", dod_path=str(root / "dod"))


def data(role, root, cfg):
    return signoff.artifact(role, STORY, cfg, str(root))


def hash_of(role, root, cfg):
    return approvals.gate_hash(f"signoff-{role}", STORY, data(role, root, cfg))


def sign(role, root, cfg, cid=11, date=T1, author=None, **kw):
    author = author or {"tpo": TPO_P, "qa": QA_P, "bpo": BPO_P}[role]
    return raw_comment(cid, f"/approve signoff-{role} {hash_of(role, root, cfg)}", date, author=author, **kw)


def run(cfg, raws, role, root, trust=TRUST, **kw):
    return signoff.check(RouteClient(cfg, raws, me=ME, **kw), trust, STORY, role, cfg, str(root))


@pytest.fixture
def cfg(tmp_path):
    return tree(tmp_path)


# ---- what each sign-off covers ----------------------------------------------------------------------------------------------------------------------

def test_the_three_gates_and_the_twelve_stay_apart():
    assert signoff.GATES == ("signoff-tpo", "signoff-qa", "signoff-bpo")
    assert not set(signoff.GATES) & set(approvals.GATES) and len(approvals.GATES) == 12


def test_tpo_covers_every_spec_dir_file_except_tasks_md(tmp_path):
    base = data("tpo", tmp_path, tree(tmp_path))
    assert base == data("tpo", tmp_path, tree(tmp_path))  # deterministic
    assert data("tpo", tmp_path, tree(tmp_path, tasks="- [x] T001 first\n- [ ] T002 added\n")) == base  # ticking or growing tasks.md signs nothing new
    for kw in ({"spec": "# Spec\nA!\n"}, {"plan": "# Plan\nC\n"}):  # one changed byte
        assert data("tpo", tmp_path, tree(tmp_path, **kw)) != base
    tree(tmp_path)
    for rel, text in (("specs/001-x/data-model.md", "x"), ("specs/001-x/contracts/api.yml", "x"), ("specs/001-x/sub/tasks.md", "x"), ("specs/001-x/.hidden", "x")):
        c = tree(tmp_path, extra={rel: text})
        assert data("tpo", tmp_path, c) != base, rel  # any other file counts, in any folder; only the top-level tasks.md is left out
        os.unlink(tmp_path / rel)
    (tmp_path / "specs/001-x/spec.md").rename(tmp_path / "specs/001-x/spec2.md")
    assert data("tpo", tmp_path, Config(organization="contoso", project="Fabrikam", dod_path=str(tmp_path / "dod"))) != base  # a renamed file


def test_tpo_is_tied_to_the_spec_dir_the_ledger_names(tmp_path):
    cfg = tree(tmp_path)
    base = data("tpo", tmp_path, cfg)
    tree(tmp_path, spec_dir="specs/002-copy")  # a byte-identical copy elsewhere, and the ledger now points at it
    assert data("tpo", tmp_path, cfg) != base
    for bad in ("../outside", "/abs/path", "specs/../specs/001-x", "specs//001-x", "specs/missing", "", "a\\b", "specs/001-x\x00"):
        (tmp_path / "dod/DoD_1001.md").write_text(ledger_md(spec_dir=bad))
        with pytest.raises(ValueError) as e:
            data("tpo", tmp_path, cfg)
        assert str(e.value) == signoff.E_ARTIFACT, bad


def test_tpo_refuses_links_odd_names_and_oversize(tmp_path):
    cfg = tree(tmp_path)
    spec = tmp_path / "specs/001-x"
    (tmp_path / "outside.md").write_text("secret")
    (spec / "link.md").symlink_to(tmp_path / "outside.md")
    for _ in (0,):
        with pytest.raises(ValueError):
            data("tpo", tmp_path, cfg)
    (spec / "link.md").unlink()
    (spec / "dir-link").symlink_to(tmp_path / "specs")
    with pytest.raises(ValueError):
        data("tpo", tmp_path, cfg)
    (spec / "dir-link").unlink()
    (spec / "bad\nname.md").write_text("x")  # a newline in a name could pass for a frame
    with pytest.raises(ValueError):
        data("tpo", tmp_path, cfg)
    (spec / "bad\nname.md").unlink()
    (spec / "big.md").write_text("x" * (1 << 20 + 1))
    with pytest.raises(ValueError):
        data("tpo", tmp_path, cfg)
    (spec / "big.md").unlink()
    (spec / "bin.dat").write_bytes(b"\xff\xfe")  # not UTF-8
    with pytest.raises(ValueError):
        data("tpo", tmp_path, cfg)
    (spec / "bin.dat").unlink()
    for n in range(signoff.MAX_FILES):
        (spec / f"f{n}.md").write_text("x")
    with pytest.raises(ValueError):
        data("tpo", tmp_path, cfg)


def test_an_empty_spec_dir_or_one_behind_a_link_has_nothing_to_sign(tmp_path):
    cfg = tree(tmp_path)
    spec = tmp_path / "specs/001-x"
    for name in ("spec.md", "plan.md"):
        (spec / name).unlink()
    for _ in (0,):  # only tasks.md is left, and it is never signed
        with pytest.raises(ValueError) as e:
            data("tpo", tmp_path, cfg)
        assert str(e.value) == signoff.E_ARTIFACT
    (spec / "tasks.md").unlink()
    (spec / "empty-dir").mkdir()
    with pytest.raises(ValueError):
        data("tpo", tmp_path, cfg)
    tree(tmp_path)
    (tmp_path / "elsewhere").mkdir()  # a folder outside the Spec dir path, linked in as a path component
    (tmp_path / "elsewhere/spec.md").write_text("x")
    (tmp_path / "specs/linked").symlink_to(tmp_path / "elsewhere")
    (tmp_path / "dod/DoD_1001.md").write_text(ledger_md(spec_dir="specs/linked"))
    with pytest.raises(ValueError):
        data("tpo", tmp_path, cfg)


def test_a_hard_link_and_more_than_8_mib_are_refused(tmp_path):
    cfg = tree(tmp_path)
    spec = tmp_path / "specs/001-x"
    (tmp_path / "outside.md").write_text("secret")
    os.link(tmp_path / "outside.md", spec / "hl.md")
    with pytest.raises(ValueError):
        data("tpo", tmp_path, cfg)
    (spec / "hl.md").unlink()
    for n in range(9):
        (spec / f"big{n}.md").write_text("x" * 1_000_000)  # each under 1 MiB, together over 8 MiB
    with pytest.raises(ValueError):
        data("tpo", tmp_path, cfg)


def test_frames_cannot_be_confused(tmp_path):
    """Two different trees never give the same bytes: each file is framed by its path and byte length."""
    cfg = tree(tmp_path)
    one = data("tpo", tmp_path, tree(tmp_path, spec="a", plan="b"))
    two = data("tpo", tmp_path, tree(tmp_path, spec="a\nplan.md 1\nb", plan=""))
    assert one != two and b"specs/001-x/spec.md 1\n" in one and b"specs/001-x/plan.md 1\n" in one
    assert cfg.dod_path  # (the tree builder returns the same config shape each time)


def test_qa_and_bpo_cover_only_the_qa_file(tmp_path):
    cfg = tree(tmp_path)
    q, b = data("qa", tmp_path, cfg), data("bpo", tmp_path, cfg)
    assert q == b and q.endswith(QA_TEXT.encode() + b"\n") and b"\n" + QA_TEXT.encode() in q
    assert hash_of("qa", tmp_path, cfg) != hash_of("bpo", tmp_path, cfg)  # the gate id is part of the hash
    assert data("qa", tmp_path, tree(tmp_path, spec="# Spec\nchanged\n", plan="# Plan\nchanged\n", tasks="x")) == q  # code and spec do not move a QA signature
    assert data("qa", tmp_path, tree(tmp_path, qa=QA_TEXT.replace("pass", "fail"))) != q  # one changed byte of QA's file
    assert data("qa", tmp_path, tree(tmp_path, qa=QA_TEXT + "\n")) != q
    for role in ("qa", "bpo"):
        (tmp_path / "dod/QA_1001.md").unlink()
        with pytest.raises(ValueError) as e:
            data(role, tmp_path, cfg)
        assert str(e.value) == signoff.E_ARTIFACT
        tree(tmp_path)
    (tmp_path / "dod/QA_1001.md").unlink()
    (tmp_path / "elsewhere.md").write_text(QA_TEXT)
    (tmp_path / "dod/QA_1001.md").symlink_to(tmp_path / "elsewhere.md")
    with pytest.raises(ValueError):
        data("qa", tmp_path, cfg)


# ---- required or not ------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("trust", [Trust([], "terminal", {}), Trust([QA_P], "comment", {}), Trust([QA_P], "comment", {}, {"map": {QA_P: ["qa"]}, "signoff": ["qa"]})])
def test_a_role_the_target_does_not_require_is_not_required(tmp_path, cfg, trust):
    for role in ("tpo", "bpo") if trust.roles else signoff.ROLES:
        client = RouteClient(cfg, [], me=ME)
        got = signoff.check(client, trust, STORY, role, cfg, str(tmp_path))
        assert got == {"gate": f"signoff-{role}", "met": True, "required": False, "note": signoff.NOT_REQUIRED} and client.calls == []  # nothing is read, not even a file
        assert signoff.offline(trust, STORY, role, cfg, str(tmp_path)) == got


def test_a_required_signoff_needs_comment_mode(tmp_path, cfg):
    t = Trust([QA_P], "terminal", {}, {"map": {QA_P: ["qa"]}, "signoff": ["qa"]})
    with pytest.raises(approvals.ApprovalRefused) as e:
        signoff.check(RouteClient(cfg, [], me=ME), t, STORY, "qa", cfg, str(tmp_path))
    assert e.value.rule == "signoff-terminal" and str(e.value) == "signoff-terminal: " + signoff.E_TERMINAL


# ---- met ------------------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["tpo", "qa", "bpo"])
def test_a_role_holders_approval_of_the_exact_bytes_is_met(tmp_path, cfg, role):
    got = run(cfg, [sign(role, tmp_path, cfg)], role, tmp_path)
    who = {"tpo": TPO_P, "qa": QA_P, "bpo": BPO_P}[role]
    assert got == {"gate": f"signoff-{role}", "mode": "comment", "met": True, "required": True, "hash": hash_of(role, tmp_path, cfg), "comment_id": 11, "author": who}


def test_a_ticked_box_in_tasks_md_keeps_the_tpo_signoff(tmp_path, cfg):
    raws = [sign("tpo", tmp_path, cfg)]
    tree(tmp_path, tasks="- [x] T001 first\n")
    assert run(cfg, raws, "tpo", tmp_path)["met"] is True


def test_the_signer_may_hold_several_roles_and_each_gate_is_its_own(tmp_path, cfg):
    raws = [sign("qa", tmp_path, cfg, author=BPO_P)]  # BPO_P holds bpo and qa
    assert run(cfg, raws, "qa", tmp_path)["author"] == BPO_P
    with pytest.raises(signoff.SignoffMissing) as e:  # but a qa approval is no bpo approval: another gate id and hash
        run(cfg, raws, "bpo", tmp_path)
    assert e.value.rule == "missing"


# ---- not met, one rule id each ---------------------------------------------------------------------------------------------------------------------

def refused(cfg, raws, role, root, **kw):
    with pytest.raises(approvals.ApprovalRefused) as e:
        run(cfg, raws, role, root, **kw)
    return e.value


def test_nothing_on_the_thread_is_missing(tmp_path, cfg):
    e = refused(cfg, [], "qa", tmp_path)
    assert isinstance(e, signoff.SignoffMissing) and e.rule == "missing" and str(e).startswith("signoff-qa is not met on story 1001 [missing]:")
    assert f"/approve signoff-qa {hash_of('qa', tmp_path, cfg)}" in str(e)


@pytest.mark.parametrize("make,rule,role", [
    (lambda r, c: [sign("qa", r, c, author=DEV_P)], "not-role", "qa"),  # an approver without the role
    (lambda r, c: [sign("qa", r, c, author=TPO_P)], "not-role", "qa"),  # a holder of another role
    (lambda r, c: [sign("tpo", r, c, author=QA_P)], "not-role", "tpo"),
    (lambda r, c: [sign("qa", r, c, author=STRANGER)], "not-approver", "qa"),
    (lambda r, c: [sign("qa", r, c, author=ANON)], "not-approver", "qa"),
    (lambda r, c: [sign("qa", r, c, author=ME)], "excluded-author", "qa"),  # the token identity can never sign
    (lambda r, c: [raw_comment(5, "[spec-kit-ado:x] hi", T0, author=QA_P), sign("qa", r, c)], "excluded-author", "qa"),  # a kit posting identity
    (lambda r, c: [sign("qa", r, c, version=2)], "edited", "qa"),
    (lambda r, c: [sign("qa", r, c, on_behalf=ME)], "on-behalf", "qa"),  # what a rules-bypass write leaves
    (lambda r, c: [raw_comment(11, f"/approve signoff-qa {'ab' * 32}", T1, author=QA_P)], "stale", "qa"),  # a hash of other bytes
    (lambda r, c: [sign("qa", r, c), raw_comment(12, f"/reject signoff-qa {hash_of('qa', r, c)}: not yet", T2, author=QA_P)], "rejected", "qa"),
    (lambda r, c: [sign("qa", r, c), raw_comment(12, f"/reject signoff-qa {'cd' * 32}", T2, author=BPO_P)], "rejected", "qa"),  # any holder's reject, whatever hash
    (lambda r, c: [sign("qa", r, c), raw_comment(5, f"[spec-kit-ado:approval-request] signoff-qa {hash_of('qa', r, c)}", T2, author=KIT)], "stale", "qa"),  # a request newer than the approval
])
def test_each_failing_case_names_its_rule(tmp_path, cfg, make, rule, role):
    e = refused(cfg, make(tmp_path, cfg), role, tmp_path)
    assert e.rule == rule and f"[{rule}]" in str(e) and "SEKRET" not in str(e)


def test_a_reject_by_a_non_holder_is_ignored_like_any_other_foreign_comment(tmp_path, cfg):
    raws = [sign("qa", tmp_path, cfg), raw_comment(12, f"/reject signoff-qa {hash_of('qa', tmp_path, cfg)}", T2, author=DEV_P)]
    assert run(cfg, raws, "qa", tmp_path)["met"] is True


def test_an_edited_comment_is_not_cured_by_a_no_op_edit_of_another_identity(tmp_path, cfg):
    c = sign("qa", tmp_path, cfg, version=2, modified_by=DEV_P)
    assert refused(cfg, [c], "qa", tmp_path, responses=[versions_response((1, c["text"], T1), (2, c["text"], T1))]).rule == "edited"


def test_a_forged_author_is_named(tmp_path, cfg):
    c = sign("qa", tmp_path, cfg)
    client = RouteClient(cfg, [c], me=ME)
    real = client.request
    client.request = lambda m, path, body=None, **k: ({"value": [{"fields": {"System.ChangedDate": T1, "System.AuthorizedAs": {"id": DEV_P}}}]} if "/revisions?" in path
                                                      else real(m, path, body, **k))  # the write was authenticated as someone else
    with pytest.raises(signoff.SignoffMissing) as e:
        signoff.check(client, TRUST, STORY, "qa", cfg, str(tmp_path))
    assert e.value.rule == "forged"


def test_an_older_valid_approval_survives_a_newer_foreign_one(tmp_path, cfg):
    good = sign("qa", tmp_path, cfg)
    foreign = raw_comment(12, f"/approve signoff-qa {'ab' * 32}", T2, author=DEV_P)  # not a holder: skipped, so it neither cancels nor supersedes
    assert run(cfg, [good, foreign], "qa", tmp_path)["comment_id"] == 11


def test_an_odd_comment_id_is_not_printed(tmp_path, cfg):
    got = run(cfg, [sign("qa", tmp_path, cfg, cid="abc<script>")], "qa", tmp_path)
    assert got["comment_id"] is None and "script" not in json.dumps(got)


def test_a_request_with_an_unreadable_time_makes_the_signoff_stale(tmp_path, cfg):
    h = hash_of("qa", tmp_path, cfg)
    req = raw_comment(5, f"[spec-kit-ado:approval-request] signoff-qa {h}", "not a date", author=KIT)
    assert refused(cfg, [sign("qa", tmp_path, cfg), req], "qa", tmp_path).rule == "stale"


def test_a_later_approval_after_a_reject_counts_and_a_malformed_newer_line_blocks(tmp_path, cfg):
    h = hash_of("qa", tmp_path, cfg)
    again = sign("qa", tmp_path, cfg, cid=13, date=T3)
    assert run(cfg, [sign("qa", tmp_path, cfg), raw_comment(12, f"/reject signoff-qa {h}", T2, author=QA_P), again], "qa", tmp_path)["comment_id"] == 13
    e = refused(cfg, [sign("qa", tmp_path, cfg), raw_comment(12, "/reject (signoff-qa)", T2, author=QA_P)], "qa", tmp_path)
    assert e.rule == "malformed-newer" and "1 newer malformed" in str(e)
    ok = [raw_comment(12, "/reject (signoff-qa)", T0, author=QA_P), sign("qa", tmp_path, cfg)]  # older than the approval: harmless
    assert run(cfg, ok, "qa", tmp_path)["met"] is True


def test_an_edited_reject_by_the_holder_still_blocks_the_older_approval(tmp_path, cfg):
    h = hash_of("qa", tmp_path, cfg)
    rej = raw_comment(12, f"/reject signoff-qa {h}: TC-2 fails", T2, author=QA_P, version=2)  # the holder fixed a typo in their own rejection
    versions = versions_response((1, rej["text"].replace("fails", "fials"), T2), (2, rej["text"], T2))  # the text really changed
    e = refused(cfg, [sign("qa", tmp_path, cfg), rej], "qa", tmp_path, responses=[versions])
    assert e.rule == "edited" and "rejection" in str(e)
    again = sign("qa", tmp_path, cfg, cid=13, date=T3)  # a fresh approval newer than the edited rejection counts, like after any reject
    assert run(cfg, [sign("qa", tmp_path, cfg), rej, again], "qa", tmp_path, responses=[versions])["comment_id"] == 13
    stranger = raw_comment(14, f"/reject signoff-qa {h}", T2, author=DEV_P, version=2)  # a non-holder's edited reject stays ignored
    assert run(cfg, [sign("qa", tmp_path, cfg), stranger], "qa", tmp_path)["met"] is True


def test_a_deeply_nested_spec_dir_is_an_artifact_refusal_not_a_crash(tmp_path, cfg):
    tree(tmp_path)
    deep = tmp_path / "specs/001-x" / "/".join("d" for _ in range(signoff.MAX_DEPTH + 2))
    deep.mkdir(parents=True)
    (deep / "x.md").write_text("x\n")
    with pytest.raises(ValueError, match="cannot be read"):
        signoff.artifact("tpo", STORY, cfg, str(tmp_path))
    ok = tmp_path / "specs/001-x/a/b"
    ok.mkdir(parents=True)  # a few levels are fine
    import shutil
    shutil.rmtree(tmp_path / "specs/001-x/d")
    (ok / "y.md").write_text("y\n")
    assert signoff.artifact("tpo", STORY, cfg, str(tmp_path))


def test_a_changed_file_after_signing_makes_the_signoff_stale(tmp_path, cfg):
    raws = [sign("qa", tmp_path, cfg), sign("tpo", tmp_path, cfg, cid=12)]
    assert run(cfg, raws, "qa", tmp_path)["met"] and run(cfg, raws, "tpo", tmp_path)["met"]
    tree(tmp_path, qa=QA_TEXT.replace("pass", "fail"))  # the agent edits one QA line
    assert refused(cfg, raws, "qa", tmp_path).rule == "stale"
    assert run(cfg, raws, "tpo", tmp_path)["met"]  # the spec did not move
    tree(tmp_path, plan="# Plan\nchanged\n")
    assert refused(cfg, raws, "tpo", tmp_path).rule == "stale"


# ---- the token and the thread ----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bypass", [True, AdoError("HTTP 403", status=403)])
def test_a_token_that_can_bypass_rules_or_is_unreadable_is_refused_before_any_comment_is_read(tmp_path, cfg, bypass):
    client = RouteClient(cfg, [sign("qa", tmp_path, cfg)], bypass=bypass, me=ME)
    with pytest.raises(approvals.ApprovalRefused) as e:
        signoff.check(client, TRUST, STORY, "qa", cfg, str(tmp_path))
    assert e.value.rule == "signoff-bypass" and str(e.value) == "signoff-bypass: " + approvals.BYPASS_REFUSAL
    assert not any("/comments" in c["path"] for c in client.calls) and all(c["method"] == "GET" for c in client.calls)


def test_a_truncated_or_unreadable_thread_is_signoff_unreadable(tmp_path, cfg):
    cut = {"comments": [raw_comment(1, "x", T0)], "continuationToken": "same"}
    client, pages = RouteClient(cfg, [], me=ME), iter([cut, cut])
    client.request = lambda method, path, body=None, **k: next(pages) if "/comments?" in path else RouteClient.request(client, method, path, body, **k)
    with pytest.raises(approvals.ApprovalRefused) as e:
        signoff.check(client, TRUST, STORY, "qa", cfg, str(tmp_path))
    assert e.value.rule == "signoff-unreadable" and str(e.value).startswith("signoff-unreadable: ")
    anon = RouteClient(cfg, [sign("qa", tmp_path, cfg)], me=ANON)  # the token identity cannot be established
    with pytest.raises(approvals.ApprovalRefused) as e:
        signoff.check(anon, TRUST, STORY, "qa", cfg, str(tmp_path))
    assert e.value.rule == "signoff-unreadable"


def test_an_unreadable_artifact_is_refused_with_its_fixed_text(tmp_path, cfg):
    (tmp_path / "dod/QA_1001.md").unlink()
    with pytest.raises(ValueError) as e:
        run(cfg, [], "qa", tmp_path)
    assert str(e.value) == signoff.E_ARTIFACT


# ---- offline ------------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["tpo", "qa", "bpo"])
def test_offline_prints_the_line_to_copy_and_it_equals_the_online_hash(tmp_path, cfg, role):
    got = signoff.offline(TRUST, STORY, role, cfg, str(tmp_path))
    h = hash_of(role, tmp_path, cfg)
    assert got == {"gate": f"signoff-{role}", "met": False, "verified": False, "hash": h, "line": f"/approve signoff-{role} {h}", "note": signoff.E_OFFLINE}
    assert run(cfg, [raw_comment(11, got["line"], T1, author={"tpo": TPO_P, "qa": QA_P, "bpo": BPO_P}[role])], role, tmp_path)["hash"] == h  # the line works as typed


# ---- the role predicate in guard and newest ------------------------------------------------------------------------------------------------------

def test_holders_is_optional_and_only_adds_not_role(cfg):
    c = {"id": 1, "author": QA_P, "modified_by": QA_P, "version": 1, "html": "x", "date": T1, "on_behalf": False}
    client = RouteClient(cfg, [], me=ME)
    assert approvals.guard(client, 2, c, [QA_P], set()) is None
    assert approvals.guard(client, 2, c, [QA_P], set(), holders=[QA_P.upper()]) is None
    assert approvals.guard(client, 2, c, [QA_P], set(), holders=[TPO_P]) == "not-role"
    assert approvals.guard(client, 2, {**c, "version": 2}, [QA_P], set(), holders=[QA_P]) == "edited"
    assert approvals.guard(client, 2, {**c, "author": DEV_P, "modified_by": DEV_P}, [QA_P], set(), holders=[DEV_P]) == "not-approver"  # approver first


# ---- the CLI ------------------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def cli(tmp_path, monkeypatch):
    c = tree(tmp_path)
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam", "dod_path": "dod"}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: TRUST)
    raws = []
    monkeypatch.setattr(_main, "AdoClient", lambda conf: RouteClient(conf, raws, me=ME))
    return c, raws


def test_cli_met_unmet_and_offline(cli, capsys):
    cfg, raws = cli
    assert _main.main(["approvals", "check", "1001", "--gate", "signoff-qa"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: signoff-qa is not met on story 1001 [missing]:") and hash_of("qa", ".", Config.load()) in err
    assert _main.main(["approvals", "check", "1001", "--gate", "signoff-qa", "--offline"]) == 2  # a line to copy, never a pass
    out = capsys.readouterr()
    shown = json.loads(out.out)
    assert shown["met"] is False and shown["verified"] is False and shown["line"].startswith("/approve signoff-qa ") and "not verified online" in out.err
    raws.append(raw_comment(11, shown["line"], T1, author=QA_P))
    assert _main.main(["approvals", "check", "1001", "--gate", "signoff-qa"]) == 0
    assert json.loads(capsys.readouterr().out)["met"] is True


def test_cli_not_required_reads_nothing(tmp_path, monkeypatch, capsys, cli):
    monkeypatch.setattr(_main, "trusted", lambda repo=".", base=None: Trust([], "terminal", {}))
    monkeypatch.setattr(_main, "AdoClient", lambda c: pytest.fail("not required: nothing is asked of ADO"))
    for argv in (["--gate", "signoff-tpo"], ["--gate", "signoff-qa", "--offline"]):
        assert _main.main(["approvals", "check", "1001", *argv]) == 0
        assert json.loads(capsys.readouterr().out)["required"] is False


def test_cli_offline_and_unknown_signoff_gates(cli, capsys):
    assert _main.main(["approvals", "check", "1001", "--gate", "review-spec", "--offline"]) == 2
    assert capsys.readouterr().err.strip() == "error: " + signoff.E_OFFLINE_GATE
    for bad in ("signoff-dev", "signoff-", "signoff-QA", "signoff-qa;x"):
        assert _main.main(["approvals", "check", "1001", "--gate", bad]) == 2
        assert capsys.readouterr().err.strip() == "error: " + approvals.E_GATE
