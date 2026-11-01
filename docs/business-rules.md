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

Pulling an Epic or Feature writes `Rules_<work item id>.md` next to the DoD ledgers (under `dod_path`). It is a **copy for developers and the checks to read, not a place to edit**: the source of truth is the Description in ADO, and the file is replaced by the next pull. (`ado rules <story id>` writes it; see [Pulling rules](#pulling-rules).) Layout, in this order and nothing else:

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

## Pulling rules

`ado rules <story id>` copies the rules of the story's Epic and Feature into the repository. It works whatever `rules.enabled` says (only prompts, the gate and strict read that key; `rules.types` defaults to Epic and Feature) and needs a token with Work Items: Read.

- **Walk.** It reads the story, then follows the parent link (`System.LinkTypes.Hierarchy-Reverse`) up to **3 links**, stopping at a cycle. An ancestor counts only when its work item type is in `rules.types` (compared without regard to case). From a link it uses one thing: the last part of the URL, which must be a whole number of 1 to 9 digits with no leading zero (`007` and `0` are refused). Anything else, or more than one parent, stops the walk with the problem `bad-parent-link` (also when ADO answers with a work item other than the one the link names); the ancestors found before it are still pulled.
- **Reads first, writes after.** Every request is a GET. All of them finish before any file is written, so an ADO error (a 404 included) means exit 1 and no file at all.
- **Files.** One `<dod_path>/Rules_<ancestor id>.md` per ancestor that has a `Business rules` section, or a problem in it (an unclosed comment that hides the section is reported, not read as "no section"). An ancestor with neither gets no file, and an old file is never deleted. An existing file that is a symlink or not a regular file is refused (exit 2) and nothing is written. Files are replaced atomically and read back. If a write fails, the files written before it stay (each one is a whole, true copy of ADO); run the command again. A file kept only because of a problem (no rule parsed from the section) can later block strict mode through `rules-problem`.
- **Re-pull.** Nothing new in ADO means no write: the bytes are compared, and the `Last pulled` date is kept when everything else is identical. When something changed, the file is rewritten with today's date. A file's CRLF line ends are kept.
- **Output.** JSON with ids, counts and keys only, never rule text or a title: `from` (the ancestor ids, nearest first), `files`, `rules` (how many are approved, draft, retired), `problems` (`id` of the work item it belongs to, `rule`, `level`, `issue`; `rule` is null for a problem of the whole section), `changed` (the ids whose file was written) and `decisions` (for an ancestor with a `Decisions_<id>.md`, the number of open and rejected lines). Exit 0; 1 ADO error (a 404, or ADO answering with another work item than asked for, included); 2 a file that cannot be replaced, an unreadable or symlinked `Decisions_<ancestor>.md`, or a write error.
- **Decisions on an Epic or Feature.** Cross-story decisions are kept in `Decisions_<feature id>.md` with the existing verbs: `ado decisions <feature id> --add ...`, then `ado decisions <feature id> --post` (preview, human yes, apply). They work for any work item id.
- **Not a security boundary.** The pull and the files are process enforcement, not a security boundary: they put the PO's rules next to the code for people and for the checks, which compare them with ADO.
