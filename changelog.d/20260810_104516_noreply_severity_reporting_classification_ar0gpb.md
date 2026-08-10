### Fixed

- **`type_vtable_changed` no longer fires from absent evidence** —
  `RecordType.vtable` is a plain list and cannot express "not captured", so
  an asymmetric DWARF capture (one side's virtual-method DIEs living in a
  translation unit only the other side's debug info covered — a differing
  `-g` level, a differently-inlined TU, ODR first-definition-wins) made an
  *identical* class look like it gained a whole vtable, reported `BREAKING`
  with no `_ZTV` symbol anywhere and no layout movement at all. An
  empty↔non-empty vtable difference now requires an independent layout
  signal — a size change (the vptr a genuinely-polymorphic class gains) or a
  virtual-base change — mirroring the tri-state discipline
  `diff_vtable_layout.py` and `diff_elf_layout.py` already apply. An unknown
  size on either side keeps the finding: the suppression needs positive
  evidence that layout held still, and is not a fallback for missing
  information.

### Added

- **Severity-blocking compatible findings are named in `scan --against`
  reports** — with `--severity-addition error` (or `--severity-quality-issues
  error`) a compatible diff exits 1, but the report named only the blocking
  category and count, giving no symbol, kind, or description for the finding
  that actually failed the scan. Those findings are now itemized when — and
  only when — severity made them the blocking cause.

- **A severity-blocking `scan` result now fails the composite Action** — the
  Action mapped a severity-scheme `scan --against` exit 1 to
  `SEVERITY_ERROR` but its scan final-gate handled only
  `BREAKING`/`API_BREAK`/`BUDGET_OVERFLOW`, so the step succeeded despite an
  explicitly blocking severity policy. `SEVERITY_ERROR` is now declared in
  `action.yml`'s scan output vocabulary (and its generated reference) too.


- **The Action reports a scan severity gate on its default text format** —
  `format: text` is the documented default and `scan` writes no JSON sidecar,
  so the verdict mapping had nothing to read and published `ERROR` (an
  operational failure) for a severity-policy result. It now falls back to the
  gate line the CLI prints on that path — read from the output file as well
  as stdout — and a severity category configured as `error` now fails the
  step whatever exit code it produced, instead of `potential_breaking=error`
  (exit 2) being waved through by `fail-on-api-break`'s `false` default.
