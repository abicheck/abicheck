<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`build-output.json` schema + `abicheck build-output validate`** — a new
  standardized, producer-agnostic contract (`abicheck.build-output/v1`) a
  project's existing build can publish once ("build once, scan many"). The
  new `abicheck build-output validate DIRECTORY` command checks a
  hand-authored or build-emitted `build-output.json` against ADR-047 §11.1:
  every declared header root is non-empty, every target's binary matches
  its digest, `evidence.projection` is safely `"declared"` (never
  `"inferred"`, reserved for a future attribution mechanism), and no
  evidence pack is shared across targets or disagrees with the target
  referencing it. See `docs/reference/build-output-schema.md`. No producer
  tooling consumes this yet — that's future G30 work (`resolve-baseline`/
  `check-target`).
