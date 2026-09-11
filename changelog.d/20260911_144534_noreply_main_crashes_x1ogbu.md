<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Repaired CI breakage left by the `scan` command retirement (ADR-068
  Phase 6)** — PR #1211 deleted the `scan` command and its scan-only
  modules but left several tests, docs, and registries referring to the
  now-gone command. Fixed `abicheck/frontends/cli/help.py`'s own leftover
  `scan`-panel registration (`--help` no longer lists a nonexistent
  command), repointed or retired the affected tests, and updated
  `docs/contribute/usecase-registry.yaml` and two `skills-src/` reference
  pages to describe the surviving `compare`-only surface.
