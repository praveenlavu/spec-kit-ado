import os
import subprocess
import sys
from pathlib import Path

from .config import ORG_RE, valid_org


class FileStore:
    """Plaintext token at ~/.azdo/<org>.token (chmod 600). The fallback backend."""

    def _path(self, organization):
        if not valid_org(organization):  # never build a path outside ~/.azdo
            raise ValueError(f"invalid organization {organization!r}: must match {ORG_RE.pattern}")
        return Path.home() / ".azdo" / f"{organization}.token"

    def get(self, organization):
        path = self._path(organization)
        if not path.is_file():
            return None
        if path.stat().st_mode & 0o077:
            print(
                f"warning: {path} is group/world-accessible; run: chmod 600 {path}",
                file=sys.stderr,
            )
        return path.read_text(encoding="utf-8").strip() or None

    def set(self, organization, secret):
        path = self._path(organization)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create with 0o600 from the start (no world-readable window; TOCTOU-safe).
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, secret.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(path, 0o600)  # ensure 0o600 even if the file pre-existed
        return f"file {path}"


class MacKeychain:
    """macOS Keychain via the built-in `security` CLI (no third-party dep)."""

    SERVICE = "ado-toolkit"

    def get(self, organization):
        r = subprocess.run(["security", "find-generic-password", "-s", self.SERVICE,
                            "-a", organization, "-w"], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return r.stdout.strip() or None

    def set(self, organization, secret):
        # shortcut: PAT is on the security(1) argv for an instant; use a pty or the Security framework if this Mac is shared
        r = subprocess.run(["security", "add-generic-password", "-U", "-s", self.SERVICE,
                            "-a", organization, "-w", secret], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError(f"keychain store failed: {r.stderr.strip()}")
        return "macOS Keychain"


class FallbackStore:
    """Try the primary backend; on any error (or absent) use the file fallback."""

    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback

    def get(self, organization):
        try:
            value = self.primary.get(organization)
        except Exception:
            value = None
        return value if value is not None else self.fallback.get(organization)

    def set(self, organization, secret):
        try:
            return self.primary.set(organization, secret)
        except Exception:
            return self.fallback.set(organization, secret)


def resolve_store():
    # Windows/Linux use the file store; a keychain backend (Credential
    # Manager / libsecret) gets added when a real user on that OS needs it.
    if sys.platform == "darwin":
        return FallbackStore(MacKeychain(), FileStore())
    return FileStore()
