# Versioning

- **One version for everything.** The `ado` CLI (`pyproject.toml`), the extension
  (`extension.yml`), the `ado-lifecycle` workflow (`workflows/ado-lifecycle/workflow.yml`)
  and the bundle (`bundle/bundle.yml`, including the component versions it pins) always
  carry the same version and move together.
- **SemVer.** A breaking change to a CLI flag or exit code, a command's arguments, a
  config key, a workflow input, or a reusable CI input is a major bump. New behaviour
  that keeps old usage working is a minor bump; fixes are a patch.
- **Tags.** Each release is an immutable `vX.Y.Z` tag with a GitHub release (the
  extension archive and the bundle artifact `ado-X.Y.Z.zip`). A moving `v0` tag points
  at the latest `v0.Y.Z`; consumer CI references it
  (`...@v0`, `ref: refs/tags/v0`). It becomes `v1` at 1.0.0. Release tags never move.
- **`v0` and breaking changes.** While the major version is 0, SemVer allows breaking changes in a minor release, and
  `v0` follows them (0.2.0 changed config keys and CI behaviour; see its Breaking notes in the CHANGELOG). A consumer who
  needs stability pins an exact tag (`@v0.4.0`). Rollback: move `v0` back to the previous release tag
  (`git tag -f v0 v0.1.1 && git push -f origin v0`); release tags themselves never move.
- **No docs-only releases.** Changes that touch only documentation, comments or the
  changelog do not get a release or move `v0`; they sit under an `Unreleased` heading in
  [CHANGELOG.md](CHANGELOG.md) until the next behavioural release.
- **Support.** Only the current major version gets fixes.
