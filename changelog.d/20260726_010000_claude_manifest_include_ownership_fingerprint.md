<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A `--dump-manifest`-driven dump's per-TU `includes`/`project_owned`
  markers (ADR-050 D1's manifest-idiom escape hatch) never reached
  `profile_fingerprint`** — `_attach_extraction_contract` built
  `declared_includes` only from the legacy CLI's `extra_includes`/
  `extra_include_labels`, both of which are always empty for a
  manifest-driven dump (`dump()`'s own mutual-exclusivity check enforces
  that). So a manifest TU declaring `includes: [{path: ../src,
  project_owned: true}]` silently had no effect on `profile_fingerprint`
  at all — the same sibling-support-root false `ScopeMismatchError`/
  spurious-drift problem D1 exists to close, just unfixed on the manifest
  path. `_attach_extraction_contract` now derives `declared_includes` from
  the manifest's own per-TU `includes` when a manifest was used, mapping a
  `project_owned` entry to its own root-relative path string as the
  per-slot fingerprint token (manifest paths are already root-relative and
  side-normalized, so — unlike the legacy CLI's labeled `--include
  old:LABEL=PATH` form — no separate user-supplied label is needed).
