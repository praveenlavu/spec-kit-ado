# Tracker boundary

Azure DevOps Boards only (Azure Repos or GitHub for code); Jira and GitHub Issues are not supported.

This page lists, for anyone who wants to port the kit to another tracker, which functions in `src/ado` talk to the tracker (Azure Boards) and which parts are the kit's own. **No adapter is built, and none is planned for 0.3.0**: this is a map of the seam, not a plug-in interface. There is no abstract class, no registry and no config key that selects another tracker, and the names below are plain module functions that may change in a minor release.

Everything not in the table below is tracker-neutral (local files, git, the ledger, the gate, the review); the table is the one place Azure DevOps is spoken to.

## What an adapter would have to implement

Each row is a function the rest of the kit calls. The first column names it as `module.function` in `src/ado/`; a doc test pins that each name listed here is defined in `src/ado`; it cannot tell whether the list is complete.

| Function | What it does against the tracker | Why it is a boundary |
|---|---|---|
| `workitems.get`, `workitems.query`, `workitems.children` | Read one work item (with relations), run a query, list the children of a parent. | Every command starts from a story; the ledger, the gate and the status line are built from these reads. |
| `workitems.create`, `workitems.update`, `workitems.comment`, `workitems.link`, `workitems.add_relation` | Create a work item, change its state or fields, add a history comment, add a related, parent or hyperlink relation. | Board writers. In the workflow and the batch verbs (`push`, `done`, `questions --post`, `decisions --post`) they are reached only through a preview and `--apply`; the single-item verbs (`ado create`, `update`, `comment`, `link`) write immediately when a person runs them. |
| `comments.fetch` | Read the story's comment thread (`with_meta` also returns authorship, edit and version facts). | The approvals (`/approve`, `/answer`, `/reject`) and the done-time cross-check read the thread, not the agent's word. |
| `questions._me`, `questions.plan_post`, `questions.apply_post` | Who the token identity is, the preview of the open questions to post, the post itself. | Open questions live on the story as one comment the kit owns. |
| `decisions.apply_post` | Post the open `D<n>` decisions and assumptions as a comment. | Same pattern: a comment the kit owns, written only after a preview. |
| `attachments.upload`, `attachments.attach`, `attachments.fetch` | Upload a file, attach it to a work item, download a work item's attachments. | The ledger is attached to the story at done. |
| `push.plan_push`, `push.apply_push` | Compare `tasks.md` with the story's child Tasks and plan, then apply, the create and update operations. | Idempotent mirroring of tasks onto the board. |
| `done.apply`, with `done._has` and `done._status` | Attach the ledger and move the story to the done state, skipping what is already done. | The one write that closes a story. |
| `prs.ensure_work_item_links`, `prs.with_ab_ref` | For an Azure Repos PR: read the PR's work-item links back and add the missing story as a Pull Request link. `with_ab_ref` puts the `AB#<id>` token in a PR title. | On GitHub the link is only that `AB#<id>` text, which Azure Boards resolves through its GitHub connection (the organization must have it set up). Another tracker would need its own convention. |
| `rules.walk`, `rules._parent` | Follow the parent links up from a story (at most three) to find the Epic or Feature that holds `Business rules`. | The hierarchy is read through `System.LinkTypes.Hierarchy-Reverse`; another tracker's parent model would replace this. |

Not in the list on purpose: the ledger, the gate (`dod`, `gate`), the review, the stack profile and kits, strict mode, `ado doctor`'s local checks, `ado status`'s stage logic and the poller's state reading. They read files and git, and take tracker facts only from the functions above. The HTTP transport (`client`, `auth`) is Azure DevOps specific too (the `_apis` URLs, the PAT as basic auth, the API versions), and would be replaced together with the rows above.

## What the kit does and what the tracker does

| The kit does | Azure DevOps / the tracker does |
|---|---|
| Copies a story's acceptance criteria into a per-story ledger and traces each to a named test. | Holds the story, its fields, its states and its work item types. |
| In the workflow and the batch verbs, previews every board or PR change, waits for a person, then applies exactly that plan (single-item verbs such as `ado update` act at once when a person runs them). | Stores the change and its history; its permissions decide who may write at all. |
| Mirrors `tasks.md` into child Tasks and keeps their states in step with the work. | Shows them on the board; owns the columns, swimlanes and the meaning of a state. |
| Reads approvers' comments (`/approve`, `/answer`, `/reject`) and ignores those written by the token's own identity or the kit's posting identity, edited after posting, quoted, or carrying `createdOnBehalfOf`. | Records who wrote each comment and when. The kit trusts that record; it cannot see past it. |
| Reads the Epic/Feature chain for `Business rules` and traces each rule into the ledger. | Owns the hierarchy and who may edit it. |
| Runs the gate and the multi-plane review locally and, in strict mode, the L1 to L3 checks; the CI templates (the GitHub ones ran once in a test repository; the Azure Pipelines ones have never run on a hosted runner) re-run the L1 and L2 checks. | Does none of this. It has no notion of test traceability. |

## What the kit deliberately does not do

- It does not replace the tracker's own functions: sprint and capacity planning, boards and queries for people, notifications, dashboards, reporting and analytics, work item templates, rules and extensions, and permissions administration all stay in Azure DevOps.
- It does not sync two-way. It reads a story at `pull`, writes only previewed changes, and never subscribes to board changes.
- The lifecycle never creates, deletes or re-parents stories, Epics or Features (the single-item verbs `ado create` and `ado link --parent` can, run by a person). Business rules are read from them; the people who own them edit them.
- It does not authenticate anyone. It uses the Personal Access Token you give it and relies on Azure DevOps to say who commented; it is process enforcement, not a security boundary.
- It does not support Jira, GitHub Issues, Linear or any other tracker. `AB#` linking, the `_apis` endpoints and the work item type names (User Story, Issue, Product Backlog Item, Epic, Feature) are Azure DevOps's. A pull request on GitHub is supported only because Azure Boards can link to GitHub.

See the [README](../README.md#what-this-does-and-does-not-do) for the guarantees the kit does make, and the [glossary](glossary.md) for the words used here.
