# Contributing

## Discipline

- **Plan before build.** No code before a written design and plan with testable
  acceptance criteria. "Done" means those criteria are met and shown with evidence (test
  output, validation output), not asserted.
- **Small tasks.** Break work into the smallest piece that can ship on its own. Work found
  mid-task becomes a new task, not an expansion of the current one.
- **TDD.** Write the test, watch it fail, implement, watch it pass.
- Run the full suite before every commit:

  ```bash
  python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # once
  .venv/bin/pytest -q -n auto
  ```

  The 28 real-repo tests in `tests/test_ci_speckit_repo.py` need the Spec Kit CLI (`specify`); install it to run them (they skip locally without it; CI installs it and fails if it is missing).

- **No third-party runtime dependencies.** The `ado` CLI is Python standard library
  only; `pytest`, `pytest-xdist` (parallel runs, about 3x faster) and `pyyaml` stay in the `dev` extra.
- **No secrets, no client data.** Never commit a token, an `ado.config.json` with real
  values, or customer data. Examples use the fictional `contoso` organization, `Fabrikam`
  project and work item `1001`.
- **Scrub check.** `scripts/scrub_check.py` scans tracked files for secret-shaped strings
  and for terms in a denylist read from `$SCRUB_DENYLIST` or an untracked local
  `.scrub-denylist`. The denylist never enters the repo. Before a commit:
  `.venv/bin/python scripts/scrub_check.py --require-denylist` (add `--history` before a
  release). CI runs it with the denylist from a repository secret.

## Commit messages (checked by CI)

Every commit has this shape; the `commit-lint` workflow fails PRs that do not match:

```
type: short imperative summary (72 characters or fewer)

What the change does and why. Name the task it implements
(for example "Task 6 - attachments") when it comes from a plan.

AC: the acceptance criteria this commit meets, with evidence
(test counts, validation output).
```

- `type` is one of `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`, `perf`,
  `build`, `revert`, optionally with a scope: `fix(gate): ...`.
- The subject is at most 72 characters; GitHub truncates longer ones in list views.
- A body is required on `feat` and `fix` commits (merge commits are not checked).
- **One task, one commit.** Do not bundle unrelated changes or split one task across
  commits. A `fix:` or `test:` commit that addresses a review finding on a task is the
  accepted exception.

## Model tiers

When agents do the work, assign models by role. Tiers are named rather than models,
because models change:

- **Frontier tier:** specs, plans, code review, the `speckit.ado.review` planes, and
  adversarial audits.
- **Next tier down:** all code and tests, including fully specified plan tasks. Code
  work never drops to the cheapest tier.
- **Cheapest tier:** mechanical non-code edits only, such as ticking ledger boxes or
  updating status lines.

When dispatching a sub-agent, set its model explicitly. This is a convention, not a gate.

## Releases

See [VERSIONING.md](VERSIONING.md).
