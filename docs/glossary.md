# Glossary

One sentence for every concept you meet, and the first setup profile that needs it. The profiles are written by
`ado setup --profile`: `starter` (advisory, nothing blocks), `team` (the gate and the review can block) and `strict` (tests
proven red before green, a verification record, refusals). `opt-in` marks a feature you switch on yourself in any profile.
The 10-minute path is the [quickstart](quickstart.md); the keys are in [configuration](configuration.md). The keys for approvers and
approval by a comment on the story, the rule for which comment counts, and the verbs that act on them (`--sync`, `ado approvals check`, the poller `ado approvals sync` and the done-time cross-check) are all in this version.

| Concept | First needed in | What it is |
|---|---|---|
| **Story** | starter | The Azure DevOps work item (its type is `story_type`) whose id every `ado` command and `speckit.ado.*` command takes. |
| **Acceptance criterion** | starter | One testable line under `### Acceptance Criteria` in the story, numbered AC-1, AC-2 and so on in the ledger. |
| **Ledger** | starter | The per-story file `<dod_path>/DoD_<id>.md` that holds the story text, the plan and its open questions, the AC-to-test table, the gate and review log, and the Definition of Done. |
| **DoD** | starter | Definition of Done: the checklist in section 5 of the ledger that must be all ticked before the story closes, and the short name of the ledger file. |
| **Question** | starter | A line `- [ ] Q<n>: ... _(open)_ · owner: PO` in ledger section 2 that `ado questions <id> --post` posts to the story, and that is closed by an answer or by an `assumed:` answer. |
| **Assumption** | starter | A `D<n>` line of kind `assumption` in the decisions file: something the agent took to be true because nobody had answered. |
| **Decision** | starter | A `D<n>` line of kind `decision` in the decisions file: a choice made while planning or building, open until the product owner validates or rejects it. |
| **Decisions file** | starter | `<dod_path>/Decisions_<id>.md`, where every assumption and decision of a story is recorded as one `D<n>` line; `ado decisions <id> --post` posts the open ones to the story. |
| **Profile** | starter | A named set of config keys written by `ado setup --profile` with `starter`, `team` or `strict`; it is not the stack profile. |
| **Advisory mode** | starter | The default `"mode": "advisory"`: checks report problems and block only what `gate.block` lists. |
| **Preview and apply** | starter | Every ADO write first writes a plan with `--plan-out` that you read, and `--apply --expect-plan` then applies only that plan or refuses. |
| **Doctor** | starter | `ado doctor`, the read-only check that prints one `ok`, `warn`, `fail` or `unknown` row, with its fix, for each part of your setup (git, Spec Kit, config, token, identity, extension, workflow, approvers, bypass, stack and kits). |
| **Status** | starter | `ado status <id>`, the read-only report that prints the story's current stage and the one command that moves it on. |
| **Stage** | starter | The first step of a story's lifecycle that is not done yet, which `ado status <id>` prints with the one command that moves it on. |
| **Stack kit** | starter | An installed Spec Kit extension for your tech stack that the phase map in `stack` hands specify, plan, tasks and implement to; with none, core Spec Kit does them. |
| **Gate** | team | `ado gate`, the report on the ledger, the tests, the open questions and the story's commits, whose checks fail the run only when named in `gate.block`. |
| **Review** | team | `speckit.ado.review`, a read of the change in eight planes whose findings are logged in the ledger, and whose severities listed in `review_block_on` stop the pull request. |
| **CI gate** | team | The template in `docs/ci-github.md` or `docs/ci-azure-pipelines.md` that runs the gate on every pull request, with the config taken from the target branch. |
| **Strict mode** | strict | `"mode": "strict"`: the gate floor and the review floor are added to your lists, and `ado pr create` and `ado done` refuse (exit 2) until the strict checks pass. |
| **Order evidence** | strict | The `red <sha> -> green <sha>` pair in each AC row of ledger section 3, which proves the tests were committed before the code that passes them. |
| **Verification record** | strict | What `ado strict verify <id>` writes into ledger section 4 after running `strict.test_command` at every red commit, green commit and `HEAD`. |
| **AC fidelity** | strict | A fresh reviewer's verdict per AC, in ledger section 4, on whether its test asserts the stated behaviour; a model reading, not proof. |
| **Override** | strict | An emergency `kind: override` decision, recorded by a human with `ado strict override`, that lets one named strict rule pass for one story. |
| **Stack profile** | strict | `ado.stack.json`, the commands and conventions of your stacks, which a human approves with `ado stack approve` and which then sets `strict.test_command`. |
| **Kit pin** | strict | An entry in `ado.kits.lock.json`, made by `ado kits pin <id>`, recording the digests of a reviewed kit so that `ado kits verify` can see when it changes. |
| **Business rules** | opt-in | Plain statements in the Description of an Epic or Feature, under a `Business rules` heading, that bind every story below it once `rules.enabled` is true. |
| **Rules file** | opt-in | `<dod_path>/Rules_<id>.md`, the copy of one ancestor's business rules that `ado rules <id>` writes next to the ledger. |
| **Rules from:** | opt-in | The ledger header line that lists the ancestors whose rules apply to the story, nearest first, or `none`. |
| **Rules reviewed:** | opt-in | The ledger header line, written by `ado rules <id> --confirm`, that records the fingerprint of each rule a human has confirmed. |
| **Approver** | team | An ADO identity (a GUID) listed in `approvers` on the target branch, a separate human who is never the agent's token, the CI service identity or a kit posting identity. |
| **Approval mode** | opt-in | `"approval": "comment"` on the target branch: gates and verdicts need a comment from an approver instead of a keypress at the terminal, while `"terminal"` stays the default. |
| **Approve and reject commands** | opt-in | The first non-empty line of an approver's comment, such as `/approve D3` or `/reject D3: reason`, which counts only when the comment is unedited, unquoted and written by an approver in person. |
| **Approval comment** | opt-in | A comment that counts as an approval because its author is in `approvers`, so the agent's own comments, quoted text and edited comments never do. |
| **Sync** | opt-in | `ado decisions <id> --sync`, which copies an approver's `/approve` or `/reject` comment on a posted decision into the local decisions file as its verdict, with the comment's id in `via`; and `ado questions <id> --sync`, which copies an approver's `/answer Q<n>: text` comment into the ledger as the PO's answer and lists the questions due under `unattended.assume_after_hours` (by server time; it never closes one). |
| **Done-time cross-check** | opt-in | With `approvers` set, `ado done` and the CI close step check that every `validated` or `rejected` line in the decisions file is backed by an approver's comment and every `posted:` marker by an unedited kit comment (rules `decisions-xcheck-marker`, `decisions-xcheck-dropped`, `decisions-xcheck-verdict`, `decisions-xcheck-truncated`, `decisions-xcheck-unreachable`); it blocks in strict mode and warns in advisory mode, so a hand-written `validated` line cannot close a story. |
| **Approvals check** | opt-in | `ado approvals check <id> --gate <gate>`, the read-only check the workflow runs after a gate, which in comment mode passes only when an approver's comment covers the gate's current artifacts or answers its open questions and decisions. |
| **Poller** | opt-in | `ado approvals sync <run id> --resume`, the command a human schedules on a local machine so that a paused run continues once an approver has commented. |
| **Unattended** | opt-in | The `unattended` object on the target branch, with `assume_after_hours` and `approval_requests`, for runs with nobody at a terminal. |
| **Workflow** | opt-in | `ado-lifecycle`, the Spec Kit workflow that runs the whole lifecycle with a human gate before every ADO write. |
| **Role** | opt-in | One of `bpo`, `tpo`, `dev`, `qa`, held by an identity in `roles.map` on the target branch (`bsa` is a name for `tpo`). It decides who may sign; see [roles](roles.md). |
| **Mode** | opt-in | The role the person at the keyboard picked with `ado mode`, kept in `.ado/mode.json`. It steers the prompts only; nothing that enforces reads it. |
| **Sign-off** | opt-in | An approver's comment `/approve signoff-<role> <hash>` on the story, for the gates `signoff-tpo`, `signoff-qa` and `signoff-bpo`; `ado done` needs every role in `roles.signoff`. The hash covers the exact bytes signed. |
| **QA file** | opt-in | `<dod_path>/QA_<id>.md`, created once by `ado qa <id>` and written only by QA: a test plan and one line per case. The `qa` and `bpo` sign-offs cover its bytes. |
