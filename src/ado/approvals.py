from . import questions
from .client import is_anonymous

KIT_MARKER = "[spec-kit-ado:"  # every comment the kit posts starts with one (questions, decisions, ...)


def excluded(client, comments):
    """Identity ids (lowercase) that can never count as an approver: the token identity (`questions._me`; unresolvable or anonymous raises
    ValueError, never a guess) and the author of any comment that starts with a `[spec-kit-ado:` marker (the kit's posting identities).
    `comments` is `comments.fetch(..., with_meta=True)` output (its authors are already lowercase GUIDs, or None and then skipped; `with_id` rows work too and are lowercased here). The anonymous identity is never added.
    shortcut: the CI token identity (design 4.2 "Who counts") is not included; the verbs that know it (P4-8, P4-13) must add it to this set."""
    out = {questions._me(client).strip().lower()}
    for c in comments:
        author = c.get("author")
        if str(c.get("text") or "").startswith(KIT_MARKER) and isinstance(author, str) and author.strip() and not is_anonymous(author):
            out.add(author.strip().lower())
    return out
