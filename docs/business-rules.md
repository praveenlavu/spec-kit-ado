# Business rules in ADO: a guide for product owners

A business rule is a plain statement that every story under an Epic or Feature must respect. For example: "Gift card purchases never earn points."

You write the rules in ADO, in the **Description** of the Epic or Feature, in a section called `Business rules`. The toolkit reads that section. It gives each rule an id, such as `BR-1201-1`, so that developers and tests can point at it.

You need no template, plugin or special markup. Type in the normal ADO editor, or paste from Word.

This page is for you, the product owner. Developers and reviewers have a separate page: [the developer and trust reference](business-rules-reference.md).

> This feature is off until the team turns it on (see [Setting it up for your team](#setting-it-up-for-your-team)). The toolkit notices changes to a rule. It does not stop anyone from editing the Description. It is process enforcement, not a security boundary.

## Write the rules

Put the section **last** in the Description. It starts at a line that reads `Business rules` and runs to the end. Copy this and fill it in. One line per line is fine. So is one paragraph or one bullet per line.

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

Here is a complete example:

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

- **One rule per `Rule <n>:` line.** The colon is required, and so is the number. Numbers run from 1 to 9999.
- **Never renumber.** The number is the rule's identity. Developers and tests refer to it, for example `BR-1201-1`. To drop a rule, set `Status: retired`. The number stays taken. A new rule gets a new number.
- **Optional lines** under a rule: `Why:`, `Example:` (as many as you like), `Owner:` and `Status:`. A missing one is only a warning. Any other line stays part of the rule. So wrapped sentences, bullets and table rows under a rule belong to it.
- **`Owner:` is a role**, never a person's name. Do not put people's contact details in a rule.
- **Examples read "When ..., then ...".**
- **Keep it short.** A rule has at most 2,000 characters. The section has at most 64 KiB and 100 rules. The whole Description has at most 1 MiB.
- **Capital letters and spacing do not matter.** `STATUS: APPROVED` and `Status : Approved` both work. So does a bullet or a number that ADO or Word puts in front of a line.

## What the status means

| Status | What it means for the team |
|---|---|
| `draft` | An idea. Developers read it but do not have to build it. This is also what a rule with no `Status:` line is. |
| `approved` | A requirement. Developers must show which acceptance criterion and which test cover it. |
| `retired` | No longer applies. The number stays taken. |

Only `approved` rules bind developers.

## Pasting from Word

Paste into the ADO editor. Then look at the result. Every rule should start on its own line with `Rule <n>:`. Bullets, bold, links and Word's hidden markup are cleaned up for you. A table of rules is **not** read. Write each rule as a `Rule <n>:` line instead. A table inside a rule is fine.

## What the toolkit tells you

When a developer starts a story, the toolkit copies your rules next to the code and lists anything it could not read. You see this in three places:

- **Questions to you.** For each mistake, and for each draft that a story would rely on, the developer's assistant may ask you in the story's comment thread. Answer there.
- **A rule without an acceptance criterion.** If no acceptance criterion of the story states an approved rule, you are asked: should the story gain an acceptance criterion in ADO, or is the rule out of scope here? If you answer "out of scope", a developer records your answer as a decision and posts it as a comment on the story. You confirm it there.
- **A blocked story.** In strict mode, a mistake in the section stops the story from moving on until you fix the Description.

If you change an approved rule after the team has built on it, the checks stop the story until a developer re-reads it and a person re-confirms. Tell the team when you change a rule that is already approved.

## When the toolkit reports a problem

The toolkit never copies your text into a problem message. It names the kind of mistake and where to look: `after BR-1201-1` (the rule written just above the line) or `before the first rule`. All the mistakes of one pull are listed together.

**Mistakes. These block the story in strict mode.**

| Problem | What it means | What to change in ADO |
|---|---|---|
| `no-number` | A line says `Rule:` with no number. | Add a number: `Rule 4: ...` |
| `bad-number` | The number is not a whole number from 1 to 9999 (`Rule 1a:`, `Rule 0:`). | Use a whole number from 1 to 9999. |
| `bad-rule-line` | A line looks like a rule but is not written `Rule <n>:` (`Rule 3 - ...`, `Rule3: ...`), or two rules share one line. | Put `Rule 3: ...` alone on its own line. Leave a space after `Rule` and a colon after the number. |
| `unparsed-rule-text` | A line looks like the start of a rule, but not as `Rule <n>:` (`Rule one:`, `R1:`, `BR-1:`). Or a `Status:`, `Why:`, `Example:` or `Owner:` line sits above the first rule. | Write it as `Rule <n>: ...`, with its `Status:` line under it. A line that is not a rule must not start with `rule`, `R1` or `BR-`. |
| `no-section-heading` | There are rules, but no `Business rules` line above them. | Add the line `Business rules` above the first rule. |
| `duplicate-number` | Two rules have the same number. Neither can be cited. | Renumber the newer one. |
| `unknown-status` | The status is not one of the three (`Status: maybe`). | Use `draft`, `approved` or `retired`. |
| `bad-status-line` | The line is not written `Status: <value>` (`Status - approved`, `Status approved`). | Write `Status: approved`. |
| `two-status` | A rule has two `Status:` lines. | Keep one. |
| `rule-too-long` | A rule has more than 2,000 characters. | Shorten it or split it into two rules. |
| `section-too-big` | The section has more than 64 KiB or 100 rules, or the Description is over 1 MiB. | Move some rules to another Feature. |
| `no-parsable-rules` | The `Business rules` line is followed by text with no `Rule <n>:` line (a table, for example). | Write each rule as `Rule <n>: ...`. |
| `unclosed-markup` | A hidden comment or style block from a paste never closes and hides the text after it. | Paste again as plain text. |

**Warnings. These never block.**

| Problem | What it means | What to change in ADO |
|---|---|---|
| `no-status` | The rule has no `Status:` line. It counts as `draft`. | Add `Status: approved` when it is binding. |
| `no-why` | The rule has no `Why:` line. | Say where the rule comes from. |
| `no-example` | The rule has no `Example:` line. | Add a "When ..., then ..." example. |
| `no-owner` | The rule has no `Owner:` line. | Add your role. |

Not every slip can be caught. A sentence that only mentions a rule ("See Rule 2") is ordinary text. So is `Rule of thumb: be kind`. After you edit, ask a developer to pull again and read the list.

## Setting it up for your team

A short quick-start for the team lead and the developers.

1. Turn it on: set `"rules": {"enabled": true}` in `ado.config.json` (see [configuration](configuration.md)).
2. The product owner adds the `Business rules` section to the Epic or Feature, as above.
3. A developer pulls the story. The pull command reads the rules. Fix the problems it lists in ADO, then pull again.
4. At the plan step, the rules are tied to acceptance criteria and then confirmed with `ado rules <story id> --confirm`.
5. Make the `gate` job a required check, and optionally the strict re-run job once you have run it (it is shipped unrun, see the CI guides). The `gate` job compares the rules in the pull request with the rules in ADO right now. Without it as a required check, nothing stops a pull request from quietly changing a rule. Under a strict target branch, a `gate` job that cannot run also fails the build. See [GitHub](ci-github.md#business-rules-the-online-drift-check) and [Azure Pipelines](ci-azure-pipelines.md#business-rules-the-online-drift-check).

Everything else is in the [developer and trust reference](business-rules-reference.md). It covers the rules file, the story's checklist file, confirming, the gate and strict checks, how the prompts use rules and the known limits.
