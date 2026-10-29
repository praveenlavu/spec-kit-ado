<!-- Added by speckit.ado.constitution (spec-kit-ado). Append below existing articles; renumber only if the project's scheme requires it. -->

## Azure DevOps delivery articles

### Article I — The story is the source of truth

The Azure DevOps story's acceptance criteria are copied into the DoD ledger as verbatim text (ADO HTML converted to plain text)
and are not reworded in the spec. If the AC is wrong or thin, fix the story in ADO and
refresh the ledger (`ado dod <id> --refresh`); do not quietly reinterpret it.

### Article II — Every AC is proven by a named test

Each acceptance criterion (AC-1…AC-n) maps to at least one named, passing test with a
real assertion, recorded in ledger section 3 with the commit or PR that made it green.
An AC without a test is not done.

### Article III — A human approves every board change

No ADO work item, state, comment, link, or attachment changes, and no PR is opened or
commented on, until a person has seen the exact change and said yes. The rules are in
`.specify/extensions/ado/rules/hitl.md`.

### Article IV — Gates advise before they block

`ado gate` and the multi-plane review report findings by default. A check blocks only
after the team opts it in (`gate.block`, `review_block_on` in `ado.config.json`).
Missing information degrades to a note, never a false failure.

### Article V — No sensitive data or secrets in ADO or git

Customer or member data never goes into work items, comments, URLs, ledgers,
attachments, or commits. Tokens are never accepted in chat, echoed, logged, or
committed; they live only where `ado setup` stores them (OS keychain or a 0600
credentials file) or in a CI secret variable (`ADO_TOKEN`).

### Article VI — One task, one commit, naming its AC

Each task ends in exactly one focused commit whose message names the task and the AC it
satisfies. Work discovered mid-task becomes a new task, not a larger commit.
