### Fixed

- **`--show-only` no longer leaves a dangling `correlated_change_kind`
  reference in JSON, SARIF, HTML, or JUnit output** — a finding kept by
  `--show-only` whose `correlated_change_kind` names a *different* finding
  the same filter excluded (e.g. `--show-only risk` keeping
  `layout_unverifiable` but dropping the `type_vtable_changed` it
  cross-references) previously still rendered/serialized the stale
  reference in every format except markdown. All four formats now clear the
  correlation on their own filtered view, matching the markdown fix;
  the underlying `Change` objects (shared across formats) are never
  mutated.

