import pytest

from ado import approvals, comments
from ado.client import AdoError
from tests.conftest import FakeClient, comment_pages, connection_data, raw_comment

ME = "AAAA1111-0000-4000-8000-00000000000A"  # the token identity; upper case on purpose
KIT, HUMAN, ANON = "4444444a-4444-4444-8444-44444444444b", "55555555-5555-4555-8555-555555555555", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
D = "2026-10-01T00:00:00Z"


def _meta(cfg, raw):
    found, truncated = comments.fetch(FakeClient(cfg, responses=comment_pages(raw)), 2, with_meta=True)
    assert not truncated
    return found


def test_excluded_contains_token_and_kit_authors(cfg):
    found = _meta(cfg, [
        raw_comment(1, "[spec-kit-ado:questions]<br>Open questions", D, author=KIT.upper()),
        raw_comment(2, "<div>[spec-kit-ado:decisions]</div>", D, author=ME),
        raw_comment(3, "/approve D1", D, author=HUMAN),
        raw_comment(4, "see [spec-kit-ado:questions] above", D, author="66666666-6666-4666-8666-666666666666"),
        raw_comment(5, "[spec-kit-ado:x]", D, author=ANON),
        raw_comment(6, "[spec-kit-ado:x]", D, author=None)])
    client = FakeClient(cfg, responses=[connection_data(ME)])
    assert approvals.excluded(client, found) == {ME.lower(), KIT}
    assert client.calls[0]["path"] == "/_apis/connectionData?api-version=7.1-preview"  # the token identity, one request


def test_excluded_without_comments_is_just_the_token_identity(cfg):
    assert approvals.excluded(FakeClient(cfg, responses=[connection_data(ME)]), []) == {ME.lower()}


def test_excluded_refuses_when_the_token_identity_is_unknown_or_anonymous(cfg):
    for resp in (connection_data(ANON), {"authenticatedUser": {}}, AdoError("HTTP 401", status=401, kind="signin")):
        with pytest.raises(ValueError):
            approvals.excluded(FakeClient(cfg, responses=[resp]), [])


def test_excluded_lowercases_with_id_rows_too(cfg):
    rows = comments.fetch(FakeClient(cfg, responses=[{"comments": [raw_comment(1, "[spec-kit-ado:x]", D, author=KIT.upper())]}]), 2, with_id=True)
    assert approvals.excluded(FakeClient(cfg, responses=[connection_data(ME)]), rows) == {ME.lower(), KIT}
