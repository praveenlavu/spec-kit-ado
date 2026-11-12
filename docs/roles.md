# Roles and sign-offs

Optional, off by default. With no `roles` key in `ado.config.json` on the target branch nothing here changes: every output of every verb is what it was.
Turn it on when a story must not close until named people have said so: the tester (`qa`), the technical product owner (`tpo`, also called BSA) and the
business owner (`bpo`).

- A **role** is `bpo`, `tpo`, `dev` or `qa`. `ado mode` records which one the person at the keyboard works as; it steers the prompts only. `bsa` is the same
  role as `tpo` (a name, not a fifth role), and `dev` is a role id only: it has no sign-off and no playbook of its own.
- A **sign-off** is an approver's comment on the story, `/approve signoff-<role> <hash>`, where the hash covers the exact bytes signed. A person with the
  web UI signs, no token and no CLI. The gates are `signoff-tpo`, `signoff-qa` and `signoff-bpo`; they are separate from the 12 workflow gates.
- `ado done` refuses to close the story until every required sign-off is met on the bytes now on disk.

The sign-offs are process enforcement, not a security boundary: see [Not promised](#not-promised).

## Recipe: QA signs off, nobody else

The smallest setup: the story cannot close until the tester has signed the QA file.

1. **Find the tester's identity id.** Ask them to comment once on any story, then run `ado comments <id> --author-ids` (one line per comment: date, author
   id, first characters). An id is a GUID. The tester must not be the identity of the token the agent uses.
2. **Edit `ado.config.json` and merge it.** The trust keys are read only from the target branch, so the change counts once it is in a merged pull request.
   Replace the GUID with the tester's:

   ```json
   {
     "approval": "comment",
     "approvers": ["11111111-2222-3333-4444-555555555555"],
     "roles": {
       "map": {"11111111-2222-3333-4444-555555555555": ["qa"]},
       "signoff": ["qa"]
     }
   }
   ```

   `approval` must be `"comment"`: a sign-off is checked from the story's comments, so in terminal mode `ado done` refuses with `signoff-terminal`. Each
   role in `signoff` must be held in `map` by an identity that is also in `approvers`, or the config is an error.
3. **Check the setup.** `ado doctor` shows the `approvers` row; it fails when an approver is the token's identity. In this recipe only the tester holds `qa`, so
   `ado mode qa` (which needs the token identity to hold the role in `roles.map`) is refused for the agent's token, and works only for a token that doctor flags: skip
   `ado mode` in a QA-only setup (the tester signs in the web UI and needs no role picked).
4. **QA writes the QA file.** `ado qa <id>` creates `<dod_path>/QA_<id>.md` once from a template and never rewrites it. QA fills `## Test plan` and one line per
   case under `## QA verification`, in their own words, then runs `ado qa <id> --check`, which lists `uncovered-ac`, `uncovered-rule`, `malformed-case` and `rules-unreadable`
   (ids and line numbers only; exit 2 when any). Commit the file with the pull request.
5. **QA signs.** `ado approvals check <id> --gate signoff-qa --offline` prints the exact line, `/approve signoff-qa <hash>`. The tester comments that line,
   first line of the comment, in the ADO web UI. A defect goes back as `/reject signoff-qa <hash>: TC-2, TC-5`: the same hash, a colon, then the failing case ids (a `/reject` with no hash is a malformed line, not a rejection).
6. **A human closes the story.** `ado done <id> --plan-out .ado/plan-done.md`, read the plan, then `--apply --expect-plan .ado/plan-done.md`. The plan lists
   `signoff qa: comment <id>` and `bytes: <commit>` it relied on.

What adding the tester to `approvers` also lets them do (they are an approver of everything, not only of QA):

- approve any of the 12 workflow gates by comment (`review-spec`, `review-plan`, `review-review` and the others);
- approve the plan of `ado push`, `ado pr create`, `ado questions --post`, `ado decisions --post` and `ado branch --link`;
- answer the story's open questions with `/answer Q<n>` (recorded by `ado questions <id> --sync`);
- validate or reject a decision with `/approve D<n>` or `/reject D<n>` (recorded by `ado decisions <id> --sync`);
- validate a rule exclusion: any approver may, a business-owner-only switch is not built;
- and, as project-wide effects: setting `approval` to `"comment"` turns on comment mode for the whole project, so the five ADO writes then need an
  approver's comment on the exact plan, and comment mode is refused while the token can bypass rules or that cannot be read; having any `approvers` means
  `ado decisions --resolve` is refused (the approvers' comments are used instead) and `ado done` and the CI close step add the done-time cross-check of the
  decisions file against the story's comments (`decisions-xcheck-*`), which fails a decision line that was resolved in a terminal before `approvers` existed.
  On Azure Repos, limit the target branch's merge types to merge (no fast-forward) and squash, or a rebase merge can drop the approvers
  (see [configuration](configuration.md#approvers-and-comment-mode-the-recommended-setup)).

If that is more than the tester should hold, keep the tester out of `approvers` and use a team member who already is one for the role; the sign-off
needs an identity that is both in `approvers` and mapped to the role.

## All three sign-offs

```json
{
  "approval": "comment",
  "approvers": ["<bpo guid>", "<tpo guid>", "<qa guid>"],
  "roles": {
    "map": {"<bpo guid>": ["bpo"], "<tpo guid>": ["tpo"], "<qa guid>": ["qa"]},
    "signoff": ["tpo", "qa", "bpo"]
  }
}
```

`roles.map` takes 1 to 50 identity ids, each with 1 to 4 distinct roles of `bpo`, `tpo`, `dev`, `qa`; `roles.signoff` takes 0 to 3 distinct roles of `bpo`,
`tpo`, `qa`. One identity may hold several roles and give several sign-offs: `ado done` shows `signoff-one-person` (a warning, never a refusal) when it
does. An anonymous id, a duplicate or an unknown key is a config error naming the position, never the value. The key table is in
[configuration](configuration.md).

## What each sign-off covers

| Gate | Signer holds | The bytes signed |
|---|---|---|
| `signoff-tpo` | `tpo` | every file in the ledger's `Spec dir`, each framed by its repository path and byte length, except the top-level `tasks.md` (ticking a task changes nothing) |
| `signoff-qa` | `qa` | the QA file `QA_<id>.md` in `dod_path` |
| `signoff-bpo` | `bpo` | the same QA file, under its own gate id, so the two hashes differ |

The hash is `sha256("spec-kit-ado/1\n" + gate + "\n" + story id + "\n" + the bytes)`. Change one byte and the sign-off is `stale`. The QA file holds only
what QA wrote, so re-running a check or a gate never changes it. The QA bytes are framed with the fixed `qa` frame label, which holds no path.

## The verbs

| Verb | What it does |
|---|---|
| `ado mode [ROLE]` | Shows, sets or (`--clear`) forgets the role in `.ado/mode.json`. `--require a,b` exits 2 unless none is picked or the picked one is listed. With `roles.map` on the target branch, picking needs the token identity to hold the role. Prompts only: nothing that enforces reads the file. |
| `ado qa <id>` | Creates the QA file once. `--check` traces the cases against the ledger's AC lines and the in-scope business rules. Local only: no token, no request. |
| `ado approvals check <id> --gate signoff-<role>` | Read only. Exit 0 when met, or when the role is not required; exit 2 with a fixed text otherwise. `--offline` needs no token: it prints the exact `/approve signoff-<role> <hash>` line from the working tree and exits 2, because it verified nothing. |
| `ado approvals request <id> --gate signoff-bpo` | Previews, and with `--apply --expect-plan FILE` posts, one comment for the business owner: each in-scope rule with its AC ids and QA results, every `D<n>` with its state, the exclusions, the `/approve` and `/reject` lines. `--apply` without the approved preview file (`--expect-plan`) is refused. It refuses when the sign-off is already met, because a posted request voids a standing approval. |
| `ado done <id>` | Once the ledger is ready, requires every role in `roles.signoff` to be met. With `bpo` required, an open `D<n>` also holds it. |

## What `ado done` does with them

- The check runs on the working tree: the merged ledger, Spec dir and QA file. Sign-offs given before a squash merge still hold, because the hash covers
  bytes, not a commit.
- Nothing is attached for them. The plan lists `signoff <role>: comment <id>` per role and `bytes: <commit>` (with a note when the tree has uncommitted
  changes), and the story's history records the same line.
- `--base` is the pull request's target branch and `origin/HEAD` the default branch; when both are strict they must agree on `roles`, `approvers` and
  `approval`, else `done` refuses with `signoff-trust`: close the story without `--base`.
- In CI the close step never closes a story that is waiting on a person: it prints `awaiting signoff-bpo` (or the roles named) as a green
  warning, exit 0, whenever every refusal is a per-role one (missing, `stale`, `rejected`, `not-role`, `edited`, `artifact`, `open-decisions` and the like), and a
  human runs `ado done --apply` once they are in. `signoff-bypass`, `signoff-unreadable`, `signoff-terminal` and `signoff-trust` stay a red failure, as does
  any non-sign-off refusal.

## Refusals

Every refusal is a fixed text; no comment text is ever shown. The rule id is in brackets.

| Rule id | Meaning | Fix |
|---|---|---|
| `signoff-<role>` with reason `missing` | no sign-off counts yet | the signer comments the line `--offline` prints |
| `stale` | the newest sign-off names other bytes, or a request is newer than it | change reverted, or sign again |
| `rejected` | the newest command is a `/reject` | read the reason in the story's comments, fix, ask again |
| `not-role` | an approver commented but does not hold the role | someone who holds it signs |
| `not-approver`, `excluded-author` | not in `approvers`, or the token identity or a kit posting identity | a trusted approver signs |
| `malformed-newer` | a newer command line by an approver on the gate is malformed (for example a `/reject` with no hash) | post a well-formed line |
| `edited`, `on-behalf`, `forged` | the comment was edited, written on someone's behalf, or its author is not who authenticated the write; an edited `/reject` by the signer still blocks, so it cannot be read as unsaid | post a new comment |
| `artifact` | what the gate covers cannot be read | see Limits |
| `open-decisions` | with `bpo` required, a `D<n>` in `Decisions_<id>.md` is still open | a person validates or rejects it: `ado decisions <id> --sync` |
| `signoff-terminal` | `approval` is not `"comment"` on the target branch | set it there |
| `signoff-bypass` | the token can bypass branch rules, so comment mode cannot be trusted | use a token without that permission |
| `signoff-unreadable` | the story's comments could not be read in full | retry; nothing is guessed |
| `signoff-trust` | `--base` and `origin/HEAD` differ in `roles`, `approvers` or `approval` | close the story without `--base` |
| `signoff-one-person` | one identity gave two or more sign-offs | a warning only |

## Limits

- **Every file in the Spec dir must be UTF-8 text under 1 MiB**, and the directory at most 200 files, 8 MiB and 32 folders deep, with no link, no hard link and no odd
  file name. Anything else is `artifact`, and the TPO sign-off is blocked until the directory is fixed. The QA file has the same 1 MiB, UTF-8 and no-link rules. A Spec dir that is empty or holds only `tasks.md` is `artifact` too: there is nothing to sign.
- A non-holder's `/reject` is ignored by design: only an approver who holds the role can reject its sign-off.
- A posted request voids a standing approval: a request comment newer than a sign-off makes it stale, which is why `ado approvals request` refuses when
  `signoff-bpo` is already met.
- A sign-off comment that was edited never counts and a deleted one is missing; who may edit or delete comments is decided by Azure DevOps. A `/reject` the signer edited keeps blocking; a deleted `/reject` cannot be told from one never posted.
- Rules whose text changed since the business owner approved are still in scope; the request comment shows the current text, not a diff.
- The workflow `ado-lifecycle` does not wait for sign-offs; `implement` is never blocked by one. The checks are at `ado done`.

## Not promised

Read this before you rely on a sign-off. A sign-off is a signed record of who said what about which bytes. It does not promise:

- that the tests ran or passed;
- that the merged code is the code QA saw;
- that code cannot merge unsigned (a pull request controls its own pipeline file);
- that the story cannot reach Done except through `ado done` (anyone who may change the story's state can set it by hand);
- that the person at the keyboard holds the mode they picked (the mode file is agent-writable);
- that a signer read the file;
- separation of duties (one person holding two roles is flagged by `signoff-one-person`, never refused);
- anything against someone who holds approver, bypass-rules or admin rights.
