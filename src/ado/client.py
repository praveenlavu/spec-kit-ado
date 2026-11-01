import http.client
import datetime
import email.utils
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .auth import auth_header


ANONYMOUS_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"  # connectionData answers 200 with this id when no credentials arrive


def is_anonymous(identity_id, descriptor=""):
    """True for the identity ADO reports when no credentials arrived (all-a id or a System:PublicAccess descriptor),
    compared after strip + lowercase. Callers must never treat it as a real user."""
    norm = lambda v: str(v or "").strip().lower()  # noqa: E731
    return norm(identity_id) == ANONYMOUS_ID or norm(descriptor).startswith("system:publicaccess")


_TYPE_KEY = re.compile(r"[A-Za-z0-9_.]{1,100}")


class AdoError(Exception):
    """Any ADO REST failure — the message carries the HTTP status + diagnosis.
    status: HTTP status or None; kind: "signin" (sign-in page, cross-host redirect, empty 401) | "http" | "network";
    type_key: the server's JSON typeKey when it is a short [A-Za-z0-9_.] token, else "".
    An error raised without any response (untrusted host, attachment too large) has status None and kind "http":
    a probe (doctor) must treat status None + kind "http" as "unknown", never as ok or as a missing scope."""

    def __init__(self, msg, status=None, kind="http", type_key=""):
        super().__init__(msg)
        self.status, self.kind, self.type_key = status, kind, type_key


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
    detail = type_key = ""
    try:
        raw = e.read()
    except Exception:
        raw = b""
    try:
        doc = json.loads(raw.decode())
        detail = doc.get("message", "")
        key = doc.get("typeKey")
        type_key = key if isinstance(key, str) and _TYPE_KEY.fullmatch(key) else ""
    except Exception:
        pass
    msg = f"HTTP {e.code} {e.reason}: {detail}".strip().rstrip(":")
    if e.code in (401, 403):
        msg += " — check PAT scope (Work Items R&W; Project and Team: Read; Code: Read for `ado branch --link`, Code: R&W for `ado pr`) and expiry."
    # ADO answers a rejected PAT with an empty 401 (R:50); a JSON 401 is a real error.
    return AdoError(msg, e.code, "signin" if e.code == 401 and not raw.strip() else "http", type_key)


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """urllib copies the Authorization header onto redirects, so never follow one that
    leaves the original scheme + host (e.g. a sign-in page answering an expired PAT)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new, old = urllib.parse.urlsplit(newurl), urllib.parse.urlsplit(req.full_url)
        if new[:2] != old[:2]:
            raise AdoError(f"HTTP {code}: ADO redirected to another host ({new.hostname}); not followed, "
                           "so the PAT is not sent there — check PAT expiry and the organization.", code, "signin")
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
    def __init__(self, config, sleep=time.sleep, attempts=None, timeout=None):
        self.config = config
        self._sleep = sleep
        self._attempts, self._timeout = attempts, timeout  # None: MAX_ATTEMPTS / TIMEOUT (doctor asks for one attempt and a short timeout)
        self._auth = None  # looked up on the first request, then reused
        self.last_date = None  # raw Date header of the newest response (success or HTTP error), None if it had none
        self._date_mono = None  # time.monotonic() when last_date was captured
        self.base = f"https://dev.azure.com/{config.organization}"

    def request(self, method, path, body=None, content_type="application/json", raw=False):
        data = None
        if body is not None:
            data = body if raw else json.dumps(body).encode()
        # Only reads are retried: a create/update that timed out may already be committed in ADO.
        retryable = method == "GET" or (method == "POST" and "/_apis/wit/wiql" in path)
        status, _, raw_body = self._send(method, f"{self.base}{path}", data, content_type, retryable)
        if status == 203 or raw_body.lstrip()[:1] == b"<":
            raise AdoError(SIGN_IN_HINT, status, "signin")
        try:
            return json.loads(raw_body.decode()) if raw_body else {}
        except ValueError:
            raise AdoError("malformed response from ADO (not JSON)", status) from None

    def get_bytes(self, url, max_bytes):
        """GET an absolute ADO url (an attachment) as raw bytes, reading at most max_bytes.
        The host is checked here, before the token is looked up, so it is never sent elsewhere."""
        if not trusted_url(url, self.config.organization):
            raise AdoError("untrusted host: only https://dev.azure.com/<org>/ or <org>.visualstudio.com; "
                           "not requested, so the PAT is not sent")
        status, ctype, body = self._send("GET", url, None, "application/json", True, max_bytes)
        # A file may start with '<' (svg, html comment in md), so only 203 or an HTML page means sign-in.
        if status == 203 or ctype.split(";")[0].strip().lower() == "text/html":
            raise AdoError(SIGN_IN_HINT, status, "signin")
        return body

    def _send(self, method, url, data, content_type, retryable, max_bytes=None):
        """(status, content type, body bytes) with retries, auth and the same-host redirect rule."""
        headers = {"Content-Type": content_type}
        if self._auth is None:
            self._auth = auth_header(self.config.organization)
        headers.update(self._auth)
        attempts = self._attempts or MAX_ATTEMPTS
        for attempt in range(attempts):
            again = retryable and attempt < attempts - 1
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with _OPENER.open(req, timeout=self._timeout or TIMEOUT) as resp:
                    raw_body = resp.read() if max_bytes is None else _read_capped(resp, max_bytes)
                    status = getattr(resp, "status", 200)
                    ctype = (getattr(resp, "headers", None) or {}).get("Content-Type", "") or ""
                    self._note_date(getattr(resp, "headers", None))
            except urllib.error.HTTPError as e:
                self._note_date(e.headers)
                # A 429 means ADO did not apply the request, so even a write is safe to retry.
                if e.code in RETRY_STATUSES and (again or (e.code == 429 and attempt < attempts - 1)):
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
                raise AdoError(last_msg, kind="network") from None
            self._sleep(2 ** attempt)

    def _note_date(self, headers):
        self.last_date = (headers or {}).get("Date")
        self._date_mono = time.monotonic()

    def server_now(self):
        """The server's clock (UTC, +-1 s: Date has second resolution) from the newest response's Date header plus the
        monotonic seconds since it arrived (immune to local wall-clock changes); one GET /_apis/connectionData if there
        is no Date yet. None for ANY unusable Date (absent, blank, unparsable, out of range, no time zone).
        A failed connectionData GET raises AdoError (callers fail closed; doctor must catch it)."""
        if self.last_date is None:
            self.get("/_apis/connectionData?api-version=7.1-preview")
        try:
            when = email.utils.parsedate_to_datetime(self.last_date or "")
            if when.tzinfo is None:  # no zone (or "-0000"): the server did not say it is UTC
                return None
            elapsed = time.monotonic() - self._date_mono if self._date_mono is not None else 0
            return when.astimezone(datetime.timezone.utc) + datetime.timedelta(seconds=int(max(elapsed, 0)))
        except (TypeError, ValueError, IndexError, OverflowError):
            return None

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body, **kw):
        return self.request("POST", path, body, **kw)

    def patch(self, path, body, **kw):
        return self.request("PATCH", path, body, **kw)
