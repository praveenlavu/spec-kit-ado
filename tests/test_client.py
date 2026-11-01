import io
import json
import urllib.error

import pytest

from ado.client import AdoClient, AdoError
from ado.config import Config


CFG = Config(organization="contoso", project="Fabrikam")


def _http_error(code, body=b'{"message":"boom"}'):
    return urllib.error.HTTPError("u", code, "reason", hdrs=None, fp=io.BytesIO(body))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_success_parses_json(monkeypatch):
    monkeypatch.setenv("ADO_TOKEN", "t")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization", "")
        return FakeResponse(json.dumps({"ok": 1}).encode())

    monkeypatch.setattr("ado.client._OPENER.open", fake_urlopen)
    client = AdoClient(CFG)
    assert client.get("/x/_apis/y?api-version=7.1") == {"ok": 1}
    assert seen["url"] == "https://dev.azure.com/contoso/x/_apis/y?api-version=7.1"
    assert seen["auth"].startswith("Basic ")


def test_401_message_names_pat_scope(monkeypatch):
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.setattr(
        "ado.client._OPENER.open", lambda req, timeout=None: (_ for _ in ()).throw(_http_error(401))
    )
    with pytest.raises(AdoError, match="401.*PAT scope"):
        AdoClient(CFG).get("/p")


def test_404_message_carries_status(monkeypatch):
    monkeypatch.setenv("ADO_TOKEN", "t")
    monkeypatch.setattr(
        "ado.client._OPENER.open", lambda req, timeout=None: (_ for _ in ()).throw(_http_error(404))
    )
    with pytest.raises(AdoError, match="404"):
        AdoClient(CFG).get("/p")


def test_503_retries_then_server_error(monkeypatch):
    monkeypatch.setenv("ADO_TOKEN", "t")
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        raise _http_error(503)

    slept = []
    monkeypatch.setattr("ado.client._OPENER.open", flaky)
    client = AdoClient(CFG, sleep=slept.append)
    with pytest.raises(AdoError, match="503"):
        client.get("/p")
    assert calls["n"] == 3
    assert slept == [1, 2]


def test_urlerror_retries_then_server_error(monkeypatch):
    monkeypatch.setenv("ADO_TOKEN", "t")
    calls = {"n": 0}

    def refused(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("Connection refused")

    slept = []
    monkeypatch.setattr("ado.client._OPENER.open", refused)
    client = AdoClient(CFG, sleep=slept.append)
    with pytest.raises(AdoError, match="network error"):
        client.get("/p")
    assert calls["n"] == 3
    assert slept == [1, 2]


def _counting(monkeypatch, outcomes):
    """urlopen fake: each call pops the next outcome (an exception to raise or a response)."""
    monkeypatch.setenv("ADO_TOKEN", "t")
    seen = []

    def fake(req, timeout=None):
        seen.append((req.get_method(), timeout))
        o = outcomes.pop(0)
        if isinstance(o, BaseException):
            raise o
        return o
    monkeypatch.setattr("ado.client._OPENER.open", fake)
    return seen


def test_post_create_503_attempted_once(monkeypatch):
    seen = _counting(monkeypatch, [_http_error(503), FakeResponse(b"{}")])
    with pytest.raises(AdoError, match="503"):
        AdoClient(CFG, sleep=lambda s: None).post("/p/_apis/wit/workitems/$Task?api-version=7.1", [])
    assert len(seen) == 1


def test_patch_network_error_attempted_once(monkeypatch):
    seen = _counting(monkeypatch, [urllib.error.URLError("reset"), FakeResponse(b"{}")])
    with pytest.raises(AdoError, match="network error"):
        AdoClient(CFG, sleep=lambda s: None).patch("/p/_apis/wit/workitems/1?api-version=7.1", [])
    assert len(seen) == 1


def test_wiql_post_503_then_200_retried(monkeypatch):
    seen = _counting(monkeypatch, [_http_error(503), FakeResponse(b'{"workItems": []}')])
    out = AdoClient(CFG, sleep=lambda s: None).post("/p/_apis/wit/wiql?api-version=7.1", {"query": "q"})
    assert out == {"workItems": []} and len(seen) == 2


def test_get_503_then_200_retried_with_timeout(monkeypatch):
    from ado.client import TIMEOUT
    seen = _counting(monkeypatch, [_http_error(503), FakeResponse(b'{"ok": 1}')])
    assert AdoClient(CFG, sleep=lambda s: None).get("/p") == {"ok": 1}
    assert seen == [("GET", TIMEOUT), ("GET", TIMEOUT)] and TIMEOUT == 30


class _ResetOnRead(FakeResponse):
    def read(self, *a):
        raise ConnectionResetError("peer reset")


def test_read_time_connection_reset_is_ado_error(monkeypatch):
    _counting(monkeypatch, [_ResetOnRead(b"")] * 3)
    with pytest.raises(AdoError, match="network error"):
        AdoClient(CFG, sleep=lambda s: None).get("/p")


def test_read_time_reset_on_create_not_retried(monkeypatch):
    seen = _counting(monkeypatch, [_ResetOnRead(b""), FakeResponse(b"{}")])
    with pytest.raises(AdoError):
        AdoClient(CFG, sleep=lambda s: None).post("/p/_apis/wit/workitems/$Task", [])
    assert len(seen) == 1


def test_remote_disconnected_is_ado_error(monkeypatch):
    import http.client
    _counting(monkeypatch, [http.client.RemoteDisconnected("gone")] * 3)
    with pytest.raises(AdoError, match="network error"):
        AdoClient(CFG, sleep=lambda s: None).get("/p")


@pytest.mark.parametrize("body", [b"not json", b"\xff\xfe"])
def test_malformed_json_is_ado_error(monkeypatch, body):
    _counting(monkeypatch, [FakeResponse(body)])
    with pytest.raises(AdoError, match="malformed"):
        AdoClient(CFG).get("/p")


def test_cli_malformed_response_exits_1(tmp_path, monkeypatch, capsys):
    from ado import __main__ as m
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "o", "project": "p"}))
    monkeypatch.chdir(tmp_path)
    _counting(monkeypatch, [FakeResponse(b"not json")])
    assert m.main(["get", "1"]) == 1
    assert "malformed" in capsys.readouterr().err


# ---- PR #1 review fix H: the PAT never follows a redirect to another host ----
def test_redirect_to_other_host_refused_same_host_followed():
    import urllib.request
    from ado import client
    h = client._SameHostRedirects()
    req = urllib.request.Request("https://dev.azure.com/contoso/_apis/x", headers={"Authorization": "Basic s3cret"})
    with pytest.raises(AdoError, match="another host") as e:
        h.redirect_request(req, None, 302, "Found", {}, "https://login.example.net/signin")
    assert "s3cret" not in str(e.value)
    with pytest.raises(AdoError):  # scheme downgrade on the same host is refused too
        h.redirect_request(req, None, 302, "Found", {}, "http://dev.azure.com/contoso/_apis/y")
    nxt = h.redirect_request(req, None, 302, "Found", {}, "https://dev.azure.com/contoso/_apis/y")
    assert nxt.full_url.endswith("/_apis/y")
    assert any(isinstance(x, client._SameHostRedirects) for x in client._OPENER.handlers)


def test_live_cross_host_redirect_never_reaches_other_host(monkeypatch):
    import http.server
    import threading
    from ado import client
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.headers["Host"], self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{srv.server_port}/signin")
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("ADO_TOKEN", "t")
        c = AdoClient(CFG, sleep=lambda s: None)
        c.base = f"http://127.0.0.1:{srv.server_port}"
        with pytest.raises(AdoError, match="another host"):
            c.get("/_apis/x")
    finally:
        srv.shutdown()
    assert [h for h, _ in hits] == [f"127.0.0.1:{srv.server_port}"]  # localhost never asked


def test_auth_header_looked_up_lazily_once_per_client(monkeypatch):
    calls = []

    def fake_header(org):
        calls.append(org)
        return {"Authorization": "Basic x"}

    monkeypatch.setattr("ado.client.auth_header", fake_header)
    monkeypatch.setattr("ado.client._OPENER.open",
                        lambda req, timeout=None: FakeResponse(b"{}"))
    client = AdoClient(CFG)
    assert calls == []  # construction never touches the token store
    client.get("/a")
    client.get("/b")
    assert calls == ["contoso"]


def _sleeps(monkeypatch, outcomes, method="get", path="/p"):
    seen, slept = _counting(monkeypatch, outcomes), []
    client = AdoClient(CFG, sleep=slept.append)
    return seen, slept, getattr(client, method)(path, *([[]] if method != "get" else []))


def _429(retry_after):
    return urllib.error.HTTPError("u", 429, "Too Many", hdrs={"Retry-After": retry_after}, fp=io.BytesIO(b"{}"))


def test_get_429_honours_retry_after_then_succeeds(monkeypatch):
    seen, slept, out = _sleeps(monkeypatch, [_429("3"), FakeResponse(b'{"ok": 1}')])
    assert out == {"ok": 1} and slept == [3] and len(seen) == 2


def test_post_429_is_retried_but_post_500_is_not(monkeypatch):
    seen, slept, out = _sleeps(monkeypatch, [_429("1"), FakeResponse(b"{}")], "post", "/p/_apis/wit/workitems/$Task")
    assert out == {} and slept == [1] and len(seen) == 2
    seen = _counting(monkeypatch, [_http_error(500), FakeResponse(b"{}")])
    with pytest.raises(AdoError, match="500"):
        AdoClient(CFG, sleep=lambda s: None).post("/p/_apis/wit/workitems/$Task", [])
    assert len(seen) == 1


def test_429_keeps_attempt_limit_on_writes(monkeypatch):
    seen = _counting(monkeypatch, [_429("1")] * 3)
    with pytest.raises(AdoError, match="429"):
        AdoClient(CFG, sleep=lambda s: None).patch("/p", [])
    assert len(seen) == 3


@pytest.mark.parametrize("value,expected", [("999", 60), ("Wed, 21 Oct 2026 07:28:00 GMT", 1), ("soon", 1), ("", 1), ("\u00b2", 1), ("9" * 5000, 1), ("-1", 1)])
def test_retry_after_capped_or_defaulted(monkeypatch, value, expected):
    _, slept, _ = _sleeps(monkeypatch, [_429(value), FakeResponse(b"{}")])
    assert slept == [expected]


class _Status203(FakeResponse):
    status = 203


@pytest.mark.parametrize("resp", [_Status203(b'{"a": 1}'), FakeResponse(b"  <html>Sign in</html>")])
def test_203_or_html_2xx_gives_pat_hint_without_leaking(monkeypatch, resp):
    _counting(monkeypatch, [resp])
    monkeypatch.setenv("ADO_TOKEN", "sekret-token")
    with pytest.raises(AdoError, match="PAT is invalid, expired or lacks scope.*ado setup") as ei:
        AdoClient(CFG).get("/p")
    assert "sekret-token" not in str(ei.value) and "Sign in" not in str(ei.value)


# ---- N2: raw-bytes GET for attachment downloads ----
ATT = "https://dev.azure.com/contoso/0000-proj/_apis/wit/attachments/1111-guid"


class _Bytes(FakeResponse):
    def __init__(self, body, ctype="application/octet-stream", status=200):
        super().__init__(body)
        self.headers = {"Content-Type": ctype}
        self.status = status


@pytest.mark.parametrize("url,ok", [
    (ATT, True),
    ("https://DEV.AZURE.COM/Contoso/p/_apis/wit/attachments/g", True),
    ("https://contoso.visualstudio.com/p/_apis/wit/attachments/g", True),
    ("http://dev.azure.com/contoso/p/_apis/wit/attachments/g", False),
    ("https://dev.azure.com/othercorp/p/_apis/wit/attachments/g", False),
    ("https://dev.azure.com.evil.com/contoso/p/_apis/wit/attachments/g", False),
    ("https://dev.azure.com@evil.com/contoso/p", False),
    ("https://dev.azure.com:8443/contoso/p", False),
    ("https://evil.com/contoso/p", False),
    ("https://evilcontoso.visualstudio.com/p", False),
    ("https://contoso.visualstudio.com.evil.com/p", False),
    ("https://dev.azure.com\\@evil.com/contoso/p", False),
    ("/contoso/p/_apis/wit/attachments/g", False),
    ("", False),
    (None, False),
])
def test_trusted_url(url, ok):
    from ado.client import trusted_url
    assert trusted_url(url, "contoso") is ok


@pytest.mark.parametrize("url", ["https://dev.azure.com.evil.com/contoso/x", "http://dev.azure.com/contoso/x",
                                 "https://dev.azure.com/othercorp/x", "https://evil.com/contoso/x"])
def test_get_bytes_untrusted_url_sends_nothing(monkeypatch, url):
    monkeypatch.setattr("ado.client.auth_header", lambda org: pytest.fail("token looked up"))
    monkeypatch.setattr("ado.client._OPENER.open", lambda *a, **k: pytest.fail("request sent"))
    with pytest.raises(AdoError, match="untrusted host"):
        AdoClient(CFG).get_bytes(url, 100)


def test_get_bytes_returns_raw_bytes_with_auth(monkeypatch):
    monkeypatch.setenv("ADO_TOKEN", "t")
    seen = {}

    def fake(req, timeout=None):
        seen.update(url=req.full_url, auth=req.headers.get("Authorization", ""), timeout=timeout)
        return _Bytes(b"<svg/>")  # an attachment may start with '<': only 203/text-html means sign-in
    monkeypatch.setattr("ado.client._OPENER.open", fake)
    assert AdoClient(CFG).get_bytes(ATT, 100) == b"<svg/>"
    assert seen["url"] == ATT and seen["auth"].startswith("Basic ") and seen["timeout"] == 30


def test_get_bytes_cap_counts_bytes_read_not_declared(monkeypatch):
    class Endless(_Bytes):
        reads = 0

        def read(self, n=-1):
            assert n is not None and 0 < n <= 11  # never an unbounded read
            Endless.reads += 1
            return b"x" * n
    _counting(monkeypatch, [_Bytes(b"x" * 10), Endless(b"")])
    client = AdoClient(CFG, sleep=lambda s: None)
    assert client.get_bytes(ATT, 10) == b"x" * 10
    with pytest.raises(AdoError, match="larger than 10 bytes"):
        client.get_bytes(ATT, 10)
    assert Endless.reads <= 2


@pytest.mark.parametrize("resp", [_Bytes(b"x", status=203), _Bytes(b"<html>Sign in</html>", ctype="text/html; charset=utf-8")])
def test_get_bytes_sign_in_page_gives_pat_hint(monkeypatch, resp):
    _counting(monkeypatch, [resp])
    with pytest.raises(AdoError, match="ado setup"):
        AdoClient(CFG).get_bytes(ATT, 100)


def test_get_bytes_429_retried(monkeypatch):
    seen = _counting(monkeypatch, [_429("2"), _Bytes(b"ok")])
    slept = []
    assert AdoClient(CFG, sleep=slept.append).get_bytes(ATT, 100) == b"ok"
    assert slept == [2] and len(seen) == 2


def test_get_bytes_cross_host_redirect_refused(monkeypatch):
    import http.server
    import threading
    from ado import client
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.headers["Host"])
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{srv.server_port}/blob")
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("ADO_TOKEN", "t")
        monkeypatch.setattr(client, "trusted_url", lambda url, org: True)  # let the test server stand in for ADO
        with pytest.raises(AdoError, match="another host"):
            AdoClient(CFG, sleep=lambda s: None).get_bytes(f"http://127.0.0.1:{srv.server_port}/att", 100)
    finally:
        srv.shutdown()
    assert hits == [f"127.0.0.1:{srv.server_port}"]


# ---- P4-1: error metadata (kind / status / type_key) and server time ----
def _hdrs(**kw):
    import email.message
    m = email.message.Message()
    for k, v in kw.items():
        m[k.replace("_", "-")] = v
    return m


class _WithHeaders(FakeResponse):
    def __init__(self, body=b"{}", status=200, **hdrs):
        super().__init__(body)
        self.status, self.headers = status, _hdrs(**hdrs)


def _err_of(monkeypatch, outcomes):
    _counting(monkeypatch, outcomes)
    with pytest.raises(AdoError) as ei:
        AdoClient(CFG, sleep=lambda s: None).get("/p")
    return ei.value


def test_signin_203_sets_kind_signin(monkeypatch):
    e = _err_of(monkeypatch, [_Status203(b'{"a": 1}')])
    assert (e.kind, e.status) == ("signin", 203)


def test_html_body_sets_kind_signin(monkeypatch):
    e = _err_of(monkeypatch, [FakeResponse(b"  <html>Sign in</html>")])
    assert (e.kind, e.status) == ("signin", 200)


def test_cross_host_302_kind_signin():
    import urllib.request
    from ado import client
    req = urllib.request.Request("https://dev.azure.com/contoso/_apis/x")
    with pytest.raises(AdoError, match="another host") as ei:
        client._SameHostRedirects().redirect_request(req, None, 302, "Found", {}, "https://login.example.net/s")
    assert (ei.value.kind, ei.value.status) == ("signin", 302)


def test_401_empty_body_kind_signin(monkeypatch):
    e = _err_of(monkeypatch, [_http_error(401, b"")])
    assert (e.kind, e.status) == ("signin", 401) and "401" in str(e) and "PAT scope" in str(e)
    e = _err_of(monkeypatch, [_http_error(401, b'{"typeKey":"UnauthorizedRequestException"}')])
    assert (e.kind, e.type_key) == ("http", "UnauthorizedRequestException")  # a JSON 401 is not a sign-in page


def test_http_error_sets_status_and_type_key(monkeypatch):
    body = json.dumps({"message": "TF200016: no", "typeKey": "ProjectDoesNotExistWithNameException"}).encode()
    e = _err_of(monkeypatch, [_http_error(404, body)])
    assert (e.status, e.kind, e.type_key) == (404, "http", "ProjectDoesNotExistWithNameException")
    assert str(e) == "HTTP 404 reason: TF200016: no"  # message unchanged


@pytest.mark.parametrize("key", ["x" * 101, "has space", "a;b", "é", "a\nb", 7, None, ["A"], ""])
def test_type_key_sanitized(monkeypatch, key):
    e = _err_of(monkeypatch, [_http_error(400, json.dumps({"message": "m", "typeKey": key}).encode())])
    assert e.type_key == ""


def test_type_key_boundary_and_non_json_body(monkeypatch):
    assert _err_of(monkeypatch, [_http_error(400, json.dumps({"typeKey": "A_b.9" * 20}).encode())]).type_key == "A_b.9" * 20
    e = _err_of(monkeypatch, [_http_error(409, b"<html>conflict</html>")])
    assert (e.status, e.kind, e.type_key) == (409, "http", "")


def test_network_error_kind(monkeypatch):
    e = _err_of(monkeypatch, [urllib.error.URLError("down")] * 3)
    assert (e.kind, e.status) == ("network", None) and "network error" in str(e)


def test_last_date_from_http_error_headers(monkeypatch):
    err = urllib.error.HTTPError("u", 404, "reason", hdrs=_hdrs(Date="Sun, 01 Nov 2026 10:00:00 GMT"),
                                 fp=io.BytesIO(b"{}"))
    _counting(monkeypatch, [err])
    c = AdoClient(CFG)
    with pytest.raises(AdoError):
        c.get("/p")
    assert c.last_date == "Sun, 01 Nov 2026 10:00:00 GMT"


def test_server_now_from_date_header(monkeypatch):
    import datetime
    seen = _counting(monkeypatch, [_WithHeaders(b'{"authenticatedUser": {}}', Date="Sun, 01 Nov 2026 10:00:05 GMT")])
    c = AdoClient(CFG)
    now = c.server_now()
    assert now == datetime.datetime(2026, 11, 1, 10, 0, 5, tzinfo=datetime.timezone.utc)
    assert now.utcoffset() == datetime.timedelta(0)
    c.server_now()
    assert len(seen) == 1  # the header is reused, no second request


def test_server_now_none_without_header(monkeypatch):
    seen = _counting(monkeypatch, [_WithHeaders(b"{}"), _WithHeaders(b"{}", Date="not a date")])
    c = AdoClient(CFG)
    assert c.server_now() is None and c.last_date is None
    c.get("/p")  # a malformed Date is kept raw but never parsed into a time
    assert c.last_date == "not a date" and c.server_now() is None and len(seen) == 2


@pytest.mark.parametrize("date", [
    " ", "", "   \t", "garbage", "Fri, 31 Dec 9999 23:30:00 -0100", "Sun, 01 Nov 20260000000000000000 10:00:00 GMT",
    "Sun, 01 Nov 2026 10:00:00", "Sun, 01 Nov 2026 10:00:00 -0000", "Sun, 32 Nov 2026 10:00:00 GMT"])
def test_server_now_none_for_any_unusable_date(monkeypatch, date):
    c = AdoClient(CFG)
    c.last_date = date
    assert c.server_now() is None  # never raises, never a guess


def test_server_now_gmt_and_offset_zones_are_utc(monkeypatch):
    import datetime
    c = AdoClient(CFG)
    c.last_date = "Sun, 01 Nov 2026 11:00:00 +0100"
    assert c.server_now() == datetime.datetime(2026, 11, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
    c.last_date = "Sun, 01 Nov 2026 10:00:00 UT"
    assert c.server_now() == datetime.datetime(2026, 11, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)


def test_failed_connection_data_get_raises_ado_error(monkeypatch):
    _counting(monkeypatch, [_http_error(403)])
    with pytest.raises(AdoError):
        AdoClient(CFG).server_now()


def test_server_now_adds_monotonic_elapsed_not_wall_clock(monkeypatch):
    import datetime
    import time
    mono = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: mono[0])
    _counting(monkeypatch, [_WithHeaders(b"{}", Date="Sun, 01 Nov 2026 10:00:00 GMT")])
    c = AdoClient(CFG)
    assert c.server_now() == datetime.datetime(2026, 11, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
    mono[0] += 30
    monkeypatch.setattr(time, "time", lambda: 4102444800.0)  # wall clock jumps to 2100: no effect
    assert c.server_now() == datetime.datetime(2026, 11, 1, 10, 0, 30, tzinfo=datetime.timezone.utc)
    mono[0] -= 50  # a (never real) backwards step must not move the server time back
    assert c.server_now() == datetime.datetime(2026, 11, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)


@pytest.mark.parametrize("ident, desc, anon", [
    ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "", True), ("AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", None, True),
    ("  aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n", "", True), ("1111", "System:PublicAccess;x", True),
    ("1111", "  system:publicaccess;x", True), ("1111", "SYSTEM:PUBLICACCESS", True),
    ("1111", "Microsoft.IdentityModel.Claims.ClaimsIdentity;x", False), ("", "", False), (None, None, False)])
def test_is_anonymous(ident, desc, anon):
    from ado.client import is_anonymous
    assert is_anonymous(ident, desc) is anon


def test_fake_client_server_time_hooks():
    from tests.conftest import FakeClient
    f = FakeClient(CFG)
    assert f.last_date is None and f.server_now() is None
    f.now = "t"
    assert f.server_now() == "t"


def test_server_now_ignores_local_clock(monkeypatch):
    import datetime
    import time
    _counting(monkeypatch, [_WithHeaders(b"{}", Date="Sun, 01 Nov 2026 10:00:00 GMT")])
    monkeypatch.setattr(time, "time", lambda: 4102444800.0)  # local clock set to 2100
    assert AdoClient(CFG).server_now() == datetime.datetime(2026, 11, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
