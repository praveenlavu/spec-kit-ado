<!-- Used by speckit.ado.pr to write .ado/pr-body.md. Replace every <placeholder>; drop this comment.
     <ledger_url> is the absolute, SHA-pinned URL built in commands/pr.md step 2; never a relative path. -->

## Summary

<two to five sentences: what changed and why, in the story's terms>

Work item: AB#<id>

## Acceptance criteria

| AC | status | tests |
|---|---|---|
| AC-1 | <green / red> | <test IDs from ledger section 3> |

Full traceability, decisions and Definition of Done: [`<dod_path>/DoD_<id>.md`](<ledger_url>)

Decisions and assumptions: `<dod_path>/Decisions_<id>.md` — <none recorded | N open or rejected: D<n> (kind), …>

## Gate (`ado gate`, <YYYY-MM-DD>)

- **Traceability:** <note from the gate output>
- **Assertion gaps:** <none | files listed>
- **AC drift:** <no drift | DRIFT — story AC changed in ADO since the ledger was seeded | not checked — <reason>>
- **Missing evidence:** <none | AC rows green without commit/PR>
- **Blocking:** <yes | no>

## Review (`speckit.ado.review`, <YYYY-MM-DD>)

- **Confirmed findings:** blocker <n> · major <n> · minor <n>
- <one line per blocker/major finding still open: plane, file:line, claim>

## Reviewer checklist

- <areas the human-review load plane says to check by hand, with rollback notes>
