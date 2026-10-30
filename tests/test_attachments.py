import pytest

from ado.attachments import BlockedFile, assert_safe, attach
from tests.conftest import FakeClient

ALLOW = [".md", ".svg", ".txt"]


def test_md_file_allowed(tmp_path):
    f = tmp_path / "design.md"
    f.write_text("# ok")
    assert assert_safe(f, ALLOW) == f


@pytest.mark.parametrize("name", ["data.csv", "export.xlsx", "scan.pdf", "img.png", "noext"])
def test_blocked_types_raise(tmp_path, name):
    f = tmp_path / name
    f.write_text("x")
    with pytest.raises(BlockedFile, match="allowlist"):
        assert_safe(f, ALLOW)


def test_missing_file_raises(tmp_path):
    with pytest.raises(BlockedFile, match="not a file"):
        assert_safe(tmp_path / "ghost.md", ALLOW)


def test_uppercase_extension_allowed(tmp_path):
    f = tmp_path / "DESIGN.MD"
    f.write_text("# ok")
    assert assert_safe(f, ALLOW) == f


def test_directory_with_allowed_name_blocked(tmp_path):
    d = tmp_path / "notes.md"
    d.mkdir()
    with pytest.raises(BlockedFile, match="not a file"):
        assert_safe(d, ALLOW)


def test_attach_uploads_then_patches_relation(cfg, tmp_path):
    doc = tmp_path / "design.md"
    doc.write_text("# design")
    client = FakeClient(cfg, responses=[{"url": "https://dev.azure.com/att/1"}, {}])
    result = attach(client, 1002, [doc], comment="US1002 design")

    assert result["attached"] == [{"path": str(doc), "url": "https://dev.azure.com/att/1"}]
    assert result["failed"] == []
    up, patch = client.calls
    assert up["method"] == "POST"
    assert "/Fabrikam/_apis/wit/attachments?fileName=design.md&api-version=7.1" in up["path"]
    assert up["content_type"] == "application/octet-stream"
    assert patch["method"] == "PATCH"
    assert "/wit/workitems/1002?api-version=7.1" in patch["path"]
    assert patch["body"][0]["value"]["rel"] == "AttachedFile"
    assert patch["body"][0]["value"]["url"] == "https://dev.azure.com/att/1"


def test_blocked_file_fails_that_file_only(cfg, tmp_path):
    bad = tmp_path / "users.csv"
    bad.write_text("sensitive")
    good = tmp_path / "notes.md"
    good.write_text("ok")
    client = FakeClient(cfg, responses=[{"url": "u2"}, {}])
    result = attach(client, 7, [bad, good])

    assert len(result["failed"]) == 1 and "allowlist" in result["failed"][0]["error"]
    assert len(result["attached"]) == 1 and result["attached"][0]["url"] == "u2"


def test_attach_custom_allowlist_allows_pdf_when_configured(cfg, tmp_path):
    cfg.attachments_allow = [".md", ".pdf"]
    pdf = tmp_path / "spec.pdf"
    pdf.write_text("x")
    client = FakeClient(cfg, responses=[{"url": "u"}, {}])
    result = attach(client, 1003, [pdf])
    assert result["failed"] == [] and len(result["attached"]) == 1


def test_attach_default_blocks_pdf(cfg, tmp_path):
    pdf = tmp_path / "spec.pdf"
    pdf.write_text("x")
    result = attach(FakeClient(cfg, responses=[]), 1003, [pdf])
    assert result["attached"] == [] and "allowlist" in result["failed"][0]["error"]


# ---- N2: list and download a story's attachments ----
import json  # noqa: E402
import os  # noqa: E402

from ado import attachments  # noqa: E402
from ado.client import AdoError  # noqa: E402

GOOD = "https://dev.azure.com/contoso/0000-proj/_apis/wit/attachments/"


def _rel(name, n, url=None, created="2026-10-28T22:00:00Z", size=7, rel="AttachedFile"):
    return {"rel": rel, "url": url or f"{GOOD}g{n}",
            "attributes": {"id": n, "name": name, "resourceSize": size, "resourceCreatedDate": created,
                           "comment": "c"}}


def _wi(*rels):
    return {"id": 2, "fields": {}, "relations": list(rels)}


@pytest.fixture
def ado_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return attachments.safe_dir(".ado/attachments/2")


def test_list_only_attached_files_without_download(cfg):
    client = FakeClient(cfg, responses=[_wi(
        _rel("product-note.md", 1, size=111),
        {"rel": "System.LinkTypes.Related", "url": "https://dev.azure.com/contoso/_apis/wit/workItems/3"},
        _rel("x.md", 9, rel="ArtifactLink"))])
    assert attachments.fetch(client, 2) == [{"name": "product-note.md", "size": 111, "id": 1}]
    assert [c["method"] for c in client.calls] == ["GET"]
    assert "/Fabrikam/_apis/wit/workitems/2?$expand=relations" in client.calls[0]["path"]


def test_no_relations_is_empty_list(cfg):
    assert attachments.fetch(FakeClient(cfg, responses=[{"id": 2}]), 2) == []


@pytest.mark.parametrize("url", ["https://evil.com/contoso/x", "http://dev.azure.com/contoso/x",
                                 "https://dev.azure.com/othercorp/x", "https://dev.azure.com.evil.com/contoso/x"])
def test_untrusted_host_skipped_and_never_requested(cfg, ado_dir, url):
    client = FakeClient(cfg, responses=[_wi(_rel("a.md", 1, url=url))])
    out = attachments.fetch(client, 2, ado_dir)
    assert out == [{"name": "a.md", "size": 7, "id": 1, "skipped": "untrusted host"}]
    assert [c["method"] for c in client.calls] == ["GET"] and not ado_dir.exists()


@pytest.mark.parametrize("name,why", [
    ("data.csv", "not on attachments_allow"), ("noext", "not on attachments_allow"),
    ("../x.md", "unsafe name"), ("a/b.md", "unsafe name"), ("a\\b.md", "unsafe name"), ("a\0.md", "unsafe name"),
    ("a\nb.md", "unsafe name"), ("a\x7fb.md", "unsafe name"), ("a\x85b.md", "unsafe name"),
    ("a\u202egpj.md", "unsafe name"),
    (".hidden.md", "unsafe name"), ("a..md", "unsafe name"), ("c:x.md", "unsafe name"), ("", "unsafe name"),
    (None, "unsafe name")])
def test_name_not_allowed_or_unsafe_skipped(cfg, ado_dir, name, why):
    client = FakeClient(cfg, responses=[_wi(_rel(name, 1))])
    assert attachments.fetch(client, 2, ado_dir)[0]["skipped"] == why
    assert len(client.calls) == 1 and not ado_dir.exists()


def test_download_writes_allowed_files_with_cap(cfg, ado_dir):
    client = FakeClient(cfg, responses=[_wi(_rel("Note.MD", 1), _rel("pic.svg", 2)), b"# note", b"<svg/>"])
    out = attachments.fetch(client, 2, ado_dir)
    assert [o["path"] for o in out] == [str(ado_dir / "Note.MD"), str(ado_dir / "pic.svg")]
    assert (ado_dir / "Note.MD").read_bytes() == b"# note"
    _, a, b = client.calls
    assert a["path"] == GOOD + "g1?api-version=7.1" and a["max_bytes"] == attachments.MAX_BYTES == 5 * 1024 * 1024
    assert sorted(os.listdir(ado_dir)) == ["Note.MD", "pic.svg"]  # no temp files left


def test_duplicate_names_get_stable_distinct_names_oldest_first(cfg, ado_dir):
    client = FakeClient(cfg, responses=[_wi(
        _rel("DoD_2.md", 2, created="2026-10-28T22:50:08Z"),
        _rel("DoD_2.md", 1, created="2026-10-28T22:50:07Z"),
        _rel("dod_2.md", 3, created="2026-10-28T22:50:09Z"),
        _rel("DoD_2 (2).md", 4, created="2026-10-28T22:50:10Z")), b"old", b"new", b"lower", b"literal"])
    out = attachments.fetch(client, 2, ado_dir)
    assert [(o["id"], os.path.basename(o["path"])) for o in out] == [
        (1, "DoD_2.md"), (2, "DoD_2 (2).md"), (3, "dod_2 (3).md"), (4, "DoD_2 (2) (2).md")]
    assert (ado_dir / "DoD_2.md").read_bytes() == b"old" and (ado_dir / "DoD_2 (2).md").read_bytes() == b"new"


def test_cap_exceeded_on_streamed_body_larger_than_declared(cfg, ado_dir, monkeypatch):
    from ado.client import AdoClient
    from tests.test_client import FakeResponse
    monkeypatch.setattr(attachments, "MAX_BYTES", 16)
    monkeypatch.setenv("ADO_TOKEN", "t")
    bodies = [FakeResponse(json.dumps(_wi(_rel("big.md", 1, size=5))).encode()), FakeResponse(b"y" * 1000)]
    monkeypatch.setattr("ado.client._OPENER.open", lambda req, timeout=None: bodies.pop(0))
    out = attachments.fetch(AdoClient(cfg, sleep=lambda s: None), 2, ado_dir)
    assert "larger than 16 bytes" in out[0]["error"] and "path" not in out[0]
    assert not ado_dir.exists() or os.listdir(ado_dir) == []


def test_write_failure_removes_partial_and_isolates_file(cfg, ado_dir, monkeypatch):
    real = os.replace
    monkeypatch.setattr(attachments.os, "replace",
                        lambda a, b: (_ for _ in ()).throw(OSError("disk full")) if str(b).endswith("a.md") else real(a, b))
    client = FakeClient(cfg, responses=[_wi(_rel("a.md", 1), _rel("b.md", 2)), b"aaa", b"bbb"])
    out = attachments.fetch(client, 2, ado_dir)
    assert "disk full" in out[0]["error"] and out[1]["path"].endswith("b.md")
    assert os.listdir(ado_dir) == ["b.md"]


def test_download_error_isolated(cfg, ado_dir):
    client = FakeClient(cfg, responses=[_wi(_rel("a.md", 1), _rel("b.md", 2)), AdoError("HTTP 404"), b"b"])
    out = attachments.fetch(client, 2, ado_dir)
    assert out[0]["error"] == "HTTP 404" and out[1]["path"].endswith("b.md")


def test_existing_symlink_in_dir_is_replaced_not_followed(cfg, ado_dir, tmp_path):
    victim = tmp_path / "victim.md"
    victim.write_text("keep")
    ado_dir.mkdir(parents=True)
    os.symlink(victim, ado_dir / "a.md")
    attachments.fetch(FakeClient(cfg, responses=[_wi(_rel("a.md", 1)), b"new"]), 2, ado_dir)
    assert victim.read_text() == "keep" and not (ado_dir / "a.md").is_symlink()
    assert (ado_dir / "a.md").read_bytes() == b"new"


@pytest.mark.parametrize("bad", ["../x", "/tmp/x", "~/x", "x", ".ado/../x", ".ado", ".ado/context", ".ado/attachments",
                                 ".ado/attachments/../context"])
def test_safe_dir_refuses_outside_ado_attachments(tmp_path, monkeypatch, bad):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="--dir must be inside .ado/attachments/"):
        attachments.safe_dir(bad)


def test_safe_dir_refuses_symlink_out(tmp_path, monkeypatch):
    work, outside = tmp_path / "work", tmp_path / "outside"
    (work / ".ado").mkdir(parents=True)
    outside.mkdir()
    os.symlink(outside, work / ".ado" / "attachments")
    monkeypatch.chdir(work)
    with pytest.raises(ValueError):
        attachments.safe_dir(".ado/attachments/2")


def test_safe_dir_accepts_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert attachments.safe_dir(".ado/attachments/2") == tmp_path.resolve() / ".ado" / "attachments" / "2"


def test_failed_download_keeps_later_duplicate_names_stable(cfg, ado_dir):
    client = FakeClient(cfg, responses=[_wi(_rel("a.md", 1, created="1"), _rel("a.md", 2, created="2")),
                                        AdoError("HTTP 500"), b"second"])
    out = attachments.fetch(client, 2, ado_dir)
    assert "error" in out[0] and out[1]["path"].endswith("a (2).md")


def test_nfc_and_nfd_names_kept_as_two_files(cfg, ado_dir):
    import unicodedata
    nfc, nfd = unicodedata.normalize("NFC", "café.md"), unicodedata.normalize("NFD", "café.md")
    assert nfc != nfd
    client = FakeClient(cfg, responses=[_wi(_rel(nfc, 1, created="1"), _rel(nfd, 2, created="2")), b"one", b"two"])
    out = attachments.fetch(client, 2, ado_dir)
    assert out[1]["path"].endswith(" (2).md") and len(os.listdir(ado_dir)) == 2
    assert sorted(p.read_bytes() for p in ado_dir.iterdir()) == [b"one", b"two"]


# ---- V3-5b fix round 1: a symlink or special file is never uploaded; the bytes are read once ----
import os  # noqa: E402

from ado import attachments  # noqa: E402,F811


def _outside(tmp_path):
    out = tmp_path / "outside" / "secret.md"
    out.parent.mkdir()
    out.write_text("TOPSECRET")
    return out


def test_symlink_is_refused_by_assert_safe_and_attach(cfg, tmp_path):
    secret = _outside(tmp_path)
    inside = tmp_path / "real.md"
    inside.write_text("fine")
    for target in (secret, inside):  # to a file outside, and to a regular file inside the tree
        link = tmp_path / f"link_{target.parent.name}.md"
        link.symlink_to(target)
        with pytest.raises(BlockedFile, match="symlink"):
            assert_safe(link, ALLOW)
        client = FakeClient(cfg, responses=[{"url": "u"}, {}])
        res = attach(client, 7, [link])
        assert client.calls == [] and res["attached"] == [] and "symlink" in res["failed"][0]["error"]
    dangling = tmp_path / "dangling.md"
    dangling.symlink_to(tmp_path / "nowhere")
    with pytest.raises(BlockedFile):
        assert_safe(dangling, ALLOW)


def test_special_file_and_oversize_are_refused(cfg, tmp_path, monkeypatch):
    fifo = tmp_path / "pipe.md"
    os.mkfifo(fifo)
    with pytest.raises(BlockedFile, match="not a file"):
        assert_safe(fifo, ALLOW)
    big = tmp_path / "big.md"
    big.write_text("x" * 17)
    monkeypatch.setattr(attachments, "MAX_BYTES", 16)
    client = FakeClient(cfg)
    res = attach(client, 7, [big])
    assert client.calls == [] and "larger than" in res["failed"][0]["error"]


def test_regular_file_still_uploads_its_exact_bytes(cfg, tmp_path):
    f = tmp_path / "ok.md"
    f.write_bytes(b"# ok \xe2\x9c\x93")
    client = FakeClient(cfg, responses=[{"url": "u"}, {}])
    assert attach(client, 7, [f])["failed"] == []
    assert client.calls[0]["body"] == b"# ok \xe2\x9c\x93"


def test_bytes_are_read_once_and_exactly_those_are_uploaded(cfg, tmp_path, monkeypatch):
    f = tmp_path / "once.md"
    f.write_bytes(b"first")
    seen = []
    real = attachments.read_safe

    def spy(path):
        data = real(path)
        seen.append(data)
        f.write_bytes(b"swapped after the read")  # a later read would now return different bytes
        return data
    monkeypatch.setattr(attachments, "read_safe", spy)
    client = FakeClient(cfg, responses=[{"url": "u"}, {}])
    assert attach(client, 7, [f])["failed"] == []
    assert seen == [b"first"] and client.calls[0]["body"] == b"first"


def test_file_swapped_for_a_symlink_after_the_check_is_not_followed(cfg, tmp_path, monkeypatch):
    secret = _outside(tmp_path)
    f = tmp_path / "late.md"
    f.write_text("fine")
    real = attachments.assert_safe

    def swap(path, allow):
        out = real(path, allow)
        f.unlink()
        f.symlink_to(secret)
        return out
    monkeypatch.setattr(attachments, "assert_safe", swap)
    client = FakeClient(cfg)
    res = attach(client, 7, [f])
    assert client.calls == [] and res["attached"] == [] and res["failed"]
