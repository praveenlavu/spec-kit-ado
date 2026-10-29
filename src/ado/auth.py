import base64
import os

from . import secretstore

TOKEN_ENV = "ADO_TOKEN"


class AuthError(Exception):
    """No credential found. Message instructs, never leaks."""


def load_token(organization):
    token = os.environ.get(TOKEN_ENV)
    if token:
        return token.strip()
    stored = secretstore.resolve_store().get(organization)
    if stored:
        return stored.strip()
    raise AuthError(
        f"No credential: set ${TOKEN_ENV}, or run `ado setup` to store a PAT "
        "(Work Items - Read & Write; Project and Team - Read; Code - Read for `ado branch --link`; Code - Read & Write for "
        "`ado pr create` on Azure Repos) in your OS keychain / ~/.azdo."
    )


def store_token(organization, secret):
    return secretstore.resolve_store().set(organization, secret)


def auth_header(organization):
    token = load_token(organization)
    b64 = base64.b64encode(f":{token}".encode()).decode()
    return {"Authorization": f"Basic {b64}"}
