<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **The GitHub Action's `mode: scan` now runs `abicheck compare` internally
  for a common subset of invocations** (ADR-068 Phase 4 item 1) — a
  single-artifact `scan --against` run in `format: json` that uses none of
  `new-library-set`/`budget`/`crosscheck`/`risk-rules`/`build-target`.
  Every documented `mode: scan` input keeps working exactly as before;
  this is an internal implementation change with no Action input/output
  change. Invocations using any of the capabilities named above, a
  directory/package `against`, a non-`json` `format`, or scan's one-build
  audit mode (no `against` resolved) still invoke `abicheck scan` directly,
  since `compare` has no equivalent for those yet. Three more cases also
  stay on (or fall back to) the legacy `scan` CLI, closing gaps a
  follow-up review found: an explicitly pinned `depth` (`compare` lacks
  scan's auto-strict pinned-depth evidence contract, so a pin with no
  evidence available used to abort loudly under `scan` but would have
  silently passed under `compare`); a scan-only flag or non-`json`
  `--format` override reaching the same gate only through `extra-args`
  rather than a dedicated Action input; and — the one genuine behavioral
  gap in the migrated `compare` path itself — a cross-source hygiene
  finding (`changes[].cross_source_evolution`) that `scan --against`'s own
  baseline mechanism keeps advisory-only but a real `abicheck compare`
  subprocess does not: detected after the fact from the compare run's own
  JSON report, which is then discarded in favor of re-running through the
  legacy `scan` CLI so the published result matches `scan`'s own semantics.
