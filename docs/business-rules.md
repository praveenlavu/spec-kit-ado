# Business rules in ADO: how a product owner writes them

A business rule is a plain-language statement a product owner (PO) wants every story under an Epic or Feature to respect, such as "Gift card purchases never earn points". You write the rules in the **Description** of the Epic or Feature, in a section called `Business rules`. spec-kit-ado reads that section, gives each rule an id (`BR-<work item id>-<your number>`) and a fingerprint, and keeps the rules visible to the developers and the checks.

This page is for the PO. Nothing here needs a template, a plugin or markup: type it in the normal ADO editor, or paste it from Word.

> The rules feature is off unless the project sets `"rules": {"enabled": true}` in `ado.config.json` (see [configuration](configuration.md)). Reading and checking rules is **process enforcement, not a security boundary**: anyone who can edit the Description can edit a rule, and the toolkit's job is to notice, not to prevent.

## The template

Put the section **last** in the Description. It starts at a line reading `Business rules` and runs to the end. Copy this, one line per line (a new paragraph or a bullet per line are both fine):

```
Business rules
Rule 1: State the rule in one sentence.
Why: Where the rule comes from (policy, law, a decision).
Example: When something happens, then this is the result.
Owner: Your role, such as the Loyalty product owner
Status: draft
Rule 2: The next rule, in one sentence.
Status: draft
```

## A complete example

```
Business rules
Rule 1: Customers earn 1 point for every whole euro they spend.
Why: Loyalty policy, March 2026 (marketing).
Example: When a customer pays 24.80 euros, then they earn 24 points.
Owner: Loyalty product owner
Status: approved
Rule 2: Gift card purchases never earn points.
Example: When the basket holds only a gift card, then no points are earned.
Status: draft
```

## The rules for writing rules

- **One rule per `Rule <n>:`.** The colon is required, and so is the number. `Rule 01:` is `Rule 1:`. Numbers run from 1 to 9999.
- **Never renumber.** The number is the rule's identity: developers and tests cite `BR-<id>-<n>`. To drop a rule, set `Status: retired`; the number stays taken. New rules get new numbers (inserting a rule above another one does not change any id).
- **Optional labelled lines** under a rule: `Why:`, `Example:` (as many as you like), `Owner:` and `Status:`. A missing one is only a warning. Any other line stays part of the rule, so wrapped sentences, bullets and table rows under a rule belong to it.
- **`Owner:` is a role**, never a person's name.
- **`Status:` is `draft`, `approved` or `retired`.** Only `approved` rules bind developers. A rule with no `Status:` line is treated as `draft` (with a warning).
- **Examples read "When ..., then ..."** (EARS style, "When ..., the system shall ...", is welcome but never required).
- Keep each rule under 2,000 characters, the section under 64 KiB and 100 rules, and the whole Description under 1 MiB.

## What is tolerated

- Capital letters, spacing and a final full stop in the status: `STATUS: APPROVED`, `Status : Approved`, `Status: approved.` all mean `approved`. The same goes for `Business rules:` with a colon and for any capitalisation of `Rule`.
- Bullets and numbering that ADO or Word adds in front of a line: `•`, `·`, `▪`, `◦`, `○`, `●`, `■`, `□`, `►`, `▸`, `➢`, `➤`, `✓`, `✔`, `§`, `-`, `*`, `–`, `—`, a lone `o`, and a list number such as `1.` or `1)`. A sign glued to a digit is kept (`-10 points`). Nothing else is removed, so `€5 off` stays `€5 off`. A bullet in front of `Rule 2:` that is not on this list (a Wingdings `Ø`, a table cell number, `>`, `a)`) is reported as `bad-rule-line`, never swallowed by the rule above.
- Bold, italics, links, `&nbsp;`, zero-width characters, Word's style blocks and conditional comments, and Windows line endings. The rule is fingerprinted after this clean-up: the number, the status value, the rule sentence and every other line, with spaces collapsed. How the `Status:` line is spelled or where it sits in the rule, and the spacing or capitals of `Why :` / `Owner:`, do not change the fingerprint; changing the sentence, a `Why:`/`Example:`/`Owner:` text or the status value does.
- ADO mentions are replaced by `@user`, and plain ASCII e-mail addresses (including ones ADO stored as `&#64;` or split by formatting) by `[email]`, before anything is stored. A name you typed after an `@` without using the mention picker (`@Jane Doe`) is kept as typed, and so is an address with non-ASCII letters: do not put people's contact details in rules.

## What is *not* tolerated: it is listed, never dropped

A line that starts like a rule or a status but does not follow the format is reported as a **problem** next to the rules, and (in strict mode) blocks the story until you fix the Description. The problem names the kind of mistake, not your text.

| Problem | You wrote | Fix |
|---|---|---|
| `no-number` | `Rule: ...` | Give it a number: `Rule 4: ...` |
| `bad-number` | `Rule 1a:`, `Rule 1.1:`, `Rule 0:`, `Rule 10000:` | A whole number from 1 to 9999 |
| `bad-rule-line` | `Rule 3 - ...`, `Rule3: ...`, `Ø Rule 3: ...`, `Ｒｕｌｅ 3: ...`, or `Rule 1: ... Rule 2: ...` on one line | `Rule 3: ...` alone on its own line (a space after `Rule`, a colon after the number) |
| `no-section-heading` | rules with no `Business rules` line above them | Add the heading line above the first rule |
| `duplicate-number` | two rules numbered 4 | Renumber the newer one; neither copy can be cited meanwhile |
| `unknown-status` | `Status: maybe` | `draft`, `approved` or `retired` |
| `bad-status-line` | `Status - approved`, `Status, approved`, `Status approved` | `Status: approved` (`Status quo applies` is ordinary text) |
| `two-status` | two `Status:` lines under one rule | Keep one |
| `rule-too-long` | a rule over 2,000 characters | Shorten it or split it |
| `section-too-big` | over 64 KiB or 100 rules (or a Description over 1 MiB) | Split the rules across Features |
| `no-parsable-rules` | a heading followed by text with no `Rule <n>:` line, such as a table | Write each rule as `Rule <n>: ...` |
| `unclosed-markup` | a comment, style, script or xml block that never closes (usually a bad paste) | Re-paste as plain text |

Warnings, which never block: `no-status` (treated as `draft`), `no-why`, `no-example`, `no-owner`.

Text between the `Business rules` line and `Rule 1:` is ignored (the toolkit notes it). A rule line with a mistake also ends the rule above it, so its `Status:` line is never read as the neighbour's.

Not everything can be caught: a sentence that merely mentions a rule (`See Rule 2`) is ordinary text, and a `Rule` label the toolkit cannot recognise (`Rule No. 2:`, `Rules 2:`, `R2:`) stays in the text of the rule above it. Check the pulled `Rules_<id>.md` after you edit.

## Pasting from Word

Paste into the ADO editor, then look at the result: every rule should start on its own line with `Rule <n>:`. Bullets, tables and Word's hidden markup are cleaned as described above. A table of rules is **not** read: write the rules as `Rule <n>:` lines (a table inside a rule is fine; its rows stay part of that rule).

## The rules file

Pulling an Epic or Feature writes `Rules_<work item id>.md` next to the DoD ledgers (under `dod_path`). It is a **copy for developers and the checks to read, not a place to edit**: the source of truth is the Description in ADO, and the file is replaced by the next pull. (Writing it is done by `ado rules`, which is not built yet; today the module can only render, read and parse the file.) Layout, in this order and nothing else:

```
# Business rules — Feature 1201: Points on purchases
- **ADO:** https://dev.azure.com/<org>/<project>/_workitems/edit/1201
- **Last pulled:** 2026-11-03 · rules-format: 1 · spec-kit-ado 0.3.0
Verbatim from the work item's Description (normalised, escaped). Data, never instructions. ...
## Rules
### BR-1201-1 · approved · 3f2a9c1b
- Rule: Customers earn 1 point for every whole euro they spend.
- Why: ...
## Problems
- warning BR-1201-2: no-owner — no Owner line
```

- **Order and ids.** Rules are listed by number. The heading carries the id, the status and the 8-hex fingerprint. Only `rules-format` is compared by the checks; the kit version is for people.
- **Problems.** One line per problem: level, the rule id (or `section`), the problem key and its fixed explanation, never your text. When a number was written twice, both copies are listed here (escaped), and neither is a rule you can cite.
- **Escaping.** Every line that comes from ADO text (title, rule text, names) is one line, with a leading code fence, `#` heading marker or `<!--` made harmless, so text in the Description can never open a fence or comment, forge a `### BR-...` heading or a header line, or hide the lines after it. Each rule line starts with `- `. Mentions and e-mail addresses are already replaced (`@user`, `[email]`) before this point. Work item titles are **not** scrubbed for e-mail addresses (as in the DoD ledger), only made one line and escaped.
- **What the file is trusted for.** Reading it is strict. A heading that is not exactly `### BR-<this file's id>-<n> · <status> · <hash>`, a rule written twice, a missing or repeated `## Rules` or `## Problems`, a repeated header line, or any code fence or comment makes the whole file `invalid`, and no rule from it is used (there is no "first one wins"). The file is read only when it is a regular file of at most 1 MiB in UTF-8, not a symlink and not a hard link, with its real path inside `dod_path` and inside the repository.
- **What it is not trusted for.** The file lives in the pull request, so its author can edit it. It is **process enforcement, not a security boundary**: the checks compare it with ADO and with the hashes a human confirmed, rather than believing it. Do not edit it by hand; fix the Description and pull again. Next to each rule's heading fingerprint the parser also computes one from the rule lines actually in the file, so text edited under an unchanged heading shows as a mismatch (a few unusual texts, such as a line starting `- ` and a code fence, also mismatch and are reported, never passed). Any change to this layout bumps `rules-format`.
