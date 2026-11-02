# Quickstart: from an ADO story to Tasks on the board

The shortest path through spec-kit-ado, with the lightest setup (the `starter` profile): install, connect, pull one story,
and follow `ado status` until its Tasks are pushed to the board. Plan for about ten minutes with a story that is ready. The
timing is a target: it has not been measured by a stopwatch in this repository. Every word you meet has one line in the
[glossary](glossary.md).

The `starter` profile is advisory: nothing blocks you. You get a per-story ledger, the open questions for the product owner
and a decisions file, and the process is only reported on. When you want more, [step 7](#7-when-you-want-more) says how.

## What you need

- Python 3.9 or later, `git`, and [Spec Kit](https://github.com/github/spec-kit) 0.9.0 or later (`specify`).
- A Spec Kit project (a git repository where `specify init` has run) with an AI agent set up in it.
- An Azure DevOps story with acceptance criteria, and its id. This page uses `1001`, in the organization `contoso` and the
  project `Fabrikam`: use your own.
- A Personal Access Token with **Work Items: Read & Write** and **Project and Team: Read**. Create it under User settings,
  Personal access tokens. Never paste it into the agent chat.

## 1. Install (about 2 minutes)

0.3.0 is not tagged yet, so the two lines below fail until it is. Until then install from a checkout of this repository:
`pipx install <path-to-spec-kit-ado>`, then, in your Spec Kit project folder, `specify extension add --dev <path-to-spec-kit-ado>`.
The README lists the other ways to install, including `uv`.

```bash
pipx install git+https://github.com/praveenlavu/spec-kit-ado@v0.3.0
# in your Spec Kit project folder:
specify extension add ado --from https://github.com/praveenlavu/spec-kit-ado/archive/refs/tags/v0.3.0.zip
```

## 2. Connect (about 2 minutes)

In **your own terminal**, in the project folder (in Claude Code: `! ado setup --profile starter`):

```bash
ado setup --profile starter
```

It asks for the organization and the project, then for the PAT through a hidden prompt, checks them against Azure DevOps and
stores the PAT outside the repository. It writes `ado.config.json` with your organization, your project, the work item types and states it detects
(`story_type`, `done_state`, `removed_state`) and the profile key `"mode": "advisory"`, and adds `.ado/` to `.gitignore`. Commit `ado.config.json`; it holds names, never a secret.

## 3. Check (about 30 seconds)

```bash
ado doctor
```

One row per check, each `ok`, `warn`, `fail` or `unknown`, with the fix next to it. Fix every `fail` and run it again. A `warn`
on `specify`, `stack` or `kits` does not stop you here. An `unknown` means doctor could not tell: it is never an `ok`.

## 4. Pull the story (about 2 minutes)

In your agent, from the project folder (Claude Code and Copilot type a hyphen: `/speckit-ado-pull 1001`):

```text
/speckit.ado.pull 1001
```

It copies the story into `dod/DoD_1001.md` (the ledger) and starts the spec from it. The agent shows each ADO change first and
waits for your yes.

## 5. Follow `ado status` (about 4 minutes)

```bash
ado status 1001
```

It prints one line, `Story 1001: <stage>. Next: <command>`, and the first stage that is not met is the answer. Do what
`Next:` says, then run `ado status 1001` again. A `Next:` that starts with `speckit.ado.` is typed in your agent; one that
starts with `ado` runs in your terminal. For this story you will usually see, in this order (the order is the order of the stages
in `ado status`):

1. `ado questions 1001 --post --plan-out .ado/plan-questions.md`: the pull left questions for the product owner, and this
   writes the comment it would post. Read `.ado/plan-questions.md`, then post exactly that plan:
   `ado questions 1001 --post --apply --expect-plan .ado/plan-questions.md`.
2. Get the questions closed. This is the step people get stuck on: in `starter` no command refuses because of an open question,
   but `ado status` does not move past `questions-answered` (`questions wait for an answer`, next `speckit.ado.plan 1001`) while
   one is open, so `ado push` never shows up as `Next:` and you loop. Either way out:
   - **The product owner answers** as a comment on the story in Azure DevOps. Then run `speckit.ado.plan 1001`: it reads the
     replies into the ledger, ticks those questions and writes `plan.md`.
   - **There is no product owner to ask:** answer as the PO yourself, in the ledger. Open `dod/DoD_1001.md`, section 2, and
     change each `- [ ] Q1: ... → _(open)_ · owner: PO` line into `- [x] Q1: ... → assumed: <what you assume> · owner: PO`, then
     record the same assumption so the product owner can see it later:
     `ado decisions 1001 --add "<what you assume>" --kind assumption --why "Q1 closed as assumed"`.
     Nothing closes a question as assumed for you here: the agent does that only inside the `ado-lifecycle` workflow, after you
     approve its `wait-po-answers` gate. Then run `speckit.ado.plan 1001`.
3. `speckit.ado.plan 1001`: the plan, as above. Run it again whenever `ado status 1001` asks for it.
4. `speckit.ado.tasks 1001`: the task list.
5. `ado push 1001 --plan-out .ado/plan-push.md`: writes the plan for the child Tasks. Read it, then create them:
   `ado push 1001 --apply --expect-plan .ado/plan-push.md`.

Every ADO write is two steps: a preview you read, then an apply that refuses if the plan changed since you read it. The agent
does both for you and asks for your yes between them.

## 6. Done when: Tasks pushed

The child Tasks are on the story in Azure DevOps, and `ado status 1001` no longer asks for `ado push`. Its next stage is
`speckit.ado.implement 1001`, which is where the coding starts. If `ado status 1001` says `board not checked`, it could not
reach Azure DevOps: run `ado doctor`, then `ado status 1001` again.

## 7. When you want more

| You want | Run | Adds |
|---|---|---|
| The gate and the review to block a pull request, and CI | `ado setup --profile team` | `gate.block`, `review_block_on` and `mode: advisory` (on a strict project this turns strict off, with a warning before you answer `y`); it also asks for `approvers` (blank writes none) |
| Tests proven red before green, and a verification record | `ado setup --profile strict` | `mode: strict` and `strict.test_command` |

On a config that exists, `ado setup --profile` shows every change as `key: old -> new` and writes only when you answer `y` in
a terminal. It never removes a key. See [configuration](configuration.md) for the keys and the [glossary](glossary.md) for the
words.
