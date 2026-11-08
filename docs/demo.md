# See it catch a faked "green" (no Azure DevOps needed)

This is a real run of the `ado` CLI on the fictional fixtures in [scripts/ci_smoke/](https://github.com/praveenlavu/spec-kit-ado/blob/main/scripts/ci_smoke) (organization `contoso`, story `1001`). It
needs no Azure DevOps account and no token. It shows the strict-mode evidence checks, not the board-write previews (those are in the
[workflow README](https://github.com/praveenlavu/spec-kit-ado/blob/main/workflows/ado-lifecycle/README.md)). Recorded 2026-11-07 with `ado` 0.3.0 on macOS; the output is cut to the first lines of each command.

Reproduce it: `python3 scripts/ci_smoke/run_local.py c d` builds the same two repositories in a temporary folder, runs the same commands and prints `ok` for each case.

## A story with valid evidence

The ledger names a red commit (the test fails) and a green commit (it passes) for acceptance criterion `AC-1`, and the target branch is in strict mode.

```text
$ ado strict mode --base main
{ "mode": "strict", "source": "target branch" }

$ ado gate 1001 --offline --base main
{ "traceability": { "evaluated": true, "mapped": ["AC-1"], "unmapped": [], "note": "all AC mapped" }, ... }

$ ado strict check 1001 --phase pr --base main
{ "phase": "pr", "mode": "strict", "ok": true, "findings": [], "overrides_used": [] }

$ ado strict verify 1001 --no-record --base main
{ "mode": "strict", "ok": true, "findings": [], "recorded": false, "runs": 3, "seconds": 0.03, ... }     (exit 0)
```

`strict verify` re-runs the project's approved test command at every red commit (it must fail) and every green commit (it must pass), so the evidence is checked, not trusted.

## The same story with a faked green commit

The ledger names a "green" commit at which the tests still fail.

```text
$ ado strict verify 1001 --no-record --base main
error: strict verify failed: 1 finding(s)
{
  "mode": "strict",
  "ok": false,
  "findings": [
    {
      "rule": "green-fails",
      "detail": "AC-1: the tests fail at the green commit 85408126c831 (exit 1)",
      "fix": "make the green commit pass the test command, or correct the green SHA"
    }
  ],
  ...
}                                                                                                          (exit 2)
```

In CI the same check fails the pull request with the rule id `green-fails`. A person decides what happens next; nothing is changed for them.
