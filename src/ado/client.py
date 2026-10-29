import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .auth import auth_header


class AdoError(Exception):
    """Any ADO REST failure — the message carries the HTTP status + diagnosis."""


RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
TIMEOUT = 30  # seconds per request; a stalled connection must not hang the CLI
MAX_RETRY_AFTER = 60  # never let a server-supplied Retry-After stall the CLI longer than this

SIGN_IN_HINT = ("ADO returned a sign-in page (HTTP 203/HTML) — the PAT is invalid, expired or lacks "
                "scope for this request; run `ado setup`")


def _retry_delay(e, attempt):
    """Seconds to wait before retrying: the server's integer Retry-After (capped), else exponential backoff.
    shortcut: an HTTP-date Retry-After is treated as unparseable (default backoff), ADO sends seconds."""
    value = (e.headers.get("Retry-After", "") if e.headers else "").strip()
    # ASCII-only and short: str.isdigit() accepts "²" and int() rejects huge digit strings.
    ok = value.isascii() and value.isdigit() and len(value) <= 6
    return min(int(value), MAX_RETRY_AFTER) if ok else 2 ** attempt


def _mapped(e):
    detail = ""
    try:
        detail = json.loads(e.read().decode()).get("message", "")
    except Exception:
        pass
    msg = f"HTTP {e.code} {e.reason}: {detail}".strip().rstrip(":")
    if e.code in (401, 403):
        msg += " — check PAT scope (Work Items R&W; Project and Team: Read; Code: Read for `ado branch --link`, Code: R&W for `ado pr`) and expiry."
    return AdoError(msg)


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """urllib copies the Authorization header onto redirects, so never follow one that
    leaves the original scheme + host (e.g. a sign-in page answering an expired PAT)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new, old = urllib.parse.urlsplit(newurl), urllib.parse.urlsplit(req.full_url)
        if new[:2] != old[:2]:
            raise AdoError(f"HTTP {code}: ADO redirected to another host ({new.hostname}); not followed, "
                           "so the PAT is not sent there — check PAT expiry and the organization.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameHostRedirects)


def trusted_url(url, org):
    """True only for https://dev.azure.com/<org>/... or https://<org>.visualstudio.com/... (no userinfo,
    no port): the only hosts an absolute ADO url may send the PAT to."""
    try:
        p = urllib.parse.urlsplit(url or "")
    except ValueError:
        return False
    host, org = (p.hostname or "").lower(), org.lower()
    if p.scheme != "https" or p.netloc.lower() != host:
        return False
    if host == "dev.azure.com":
        return p.path.lower().split("/")[1:2] == [org]  # first path segment is the organization
    return host == f"{org}.visualstudio.com"


def _read_capped(resp, max_bytes):
    """Read until EOF, refusing once more than max_bytes arrive (whatever size the server declared)."""
    buf = bytearray()
    while chunk := resp.read(min(65536, max_bytes + 1 - len(buf))):
        buf += chunk
        if len(buf) > max_bytes:
            raise AdoError(f"attachment is larger than {max_bytes} bytes; refused")
    return bytes(buf)


class AdoClient:
    def __init__(self, config, sleep=time.sleep):
        self.config = config
        self._sleep = sleep
        self._auth = None  # looked up on the first request, then reused
        self.base = f"https://dev.azure.com/{config.organization}"

    def request(self, method, path, body=None, content_type="application/json", raw=False):
        data = None
        if body is not None:
            data = body if raw else json.dumps(body).encode()
        # Only reads are retried: a create/update that timed out may already be committed in ADO.
        retryable = method == "GET" or (method == "POST" and "/_apis/wit/wiql" in path)
        status, _, raw_body = self._send(method, f"{self.base}{path}", data, content_type, retryable)
        if status == 203 or raw_body.lstrip()[:1] == b"<":
            raise AdoError(SIGN_IN_HINT)
        try:
            return json.loads(raw_body.decode()) if raw_body else {}
        except ValueError:
            raise AdoError("malformed response from ADO (not JSON)") from None

    def get_bytes(self, url, max_bytes):
        """GET an absolute ADO url (an attachment) as raw bytes, reading at most max_bytes.
        The host is checked here, before the token is looked up, so it is never sent elsewhere."""
        if not trusted_url(url, self.config.organization):
            raise AdoError("untrusted host: only https://dev.azure.com/<org>/ or <org>.visualstudio.com; "
                           "not requested, so the PAT is not sent")
        status, ctype, body = self._send("GET", url, None, "application/json", True, max_bytes)
        # A file may start with '<' (svg, html comment in md), so only 203 or an HTML page means sign-in.
        if status == 203 or ctype.split(";")[0].strip().lower() == "text/html":
            raise AdoError(SIGN_IN_HINT)
        return body

    def _send(self, method, url, data, content_type, retryable, max_bytes=None):
        """(status, content type, body bytes) with retries, auth and the same-host redirect rule."""
        headers = {"Content-Type": content_type}
        if self._auth is None:
            self._auth = auth_header(self.config.organization)
        headers.update(self._auth)
        for attempt in range(MAX_ATTEMPTS):
            again = retryable and attempt < MAX_ATTEMPTS - 1
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with _OPENER.open(req, timeout=TIMEOUT) as resp:
                    raw_body = resp.read() if max_bytes is None else _read_capped(resp, max_bytes)
                    status = getattr(resp, "status", 200)
                    ctype = (getattr(resp, "headers", None) or {}).get("Content-Type", "") or ""
            except urllib.error.HTTPError as e:
                # A 429 means ADO did not apply the request, so even a write is safe to retry.
                if e.code in RETRY_STATUSES and (again or (e.code == 429 and attempt < MAX_ATTEMPTS - 1)):
                    self._sleep(_retry_delay(e, attempt))
                    continue
                raise _mapped(e) from None
            except urllib.error.URLError as e:
                last_msg = f"network error: {e.reason}"
            except (OSError, http.client.HTTPException) as e:
                # Raised while waiting for or reading the response (reset, timeout, IncompleteRead).
                last_msg = f"network error: {e.__class__.__name__}: {e}"
            else:
                return status, ctype, raw_body
            if not again:
                raise AdoError(last_msg) from None
            self._sleep(2 ** attempt)

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body, **kw):
        return self.request("POST", path, body, **kw)

    def patch(self, path, body, **kw):
        return self.request("PATCH", path, body, **kw)
