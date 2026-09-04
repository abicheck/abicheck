### Fixed

- **DWARF CFI extraction now consults every unwind section with real FDE
  data, not just the first one** — a valid binary can link object files
  built with different unwind-table settings, so `.eh_frame` can carry
  FDEs for only some exported functions while `.debug_frame` carries the
  rest; the previous single-source lookup wrongly counted a function named
  only by the unreturned section as uncovered. The per-function CFI
  coverage check is also now restricted to architectures this module has
  verified register-name support for (x64/x86/aarch64): on big-endian
  PPC64 ELFv1, an exported function symbol's address names its `.opd`
  function descriptor rather than its code entry, so every exported
  function would otherwise read "uncovered" even on a fully-instrumented
  binary.
- **PDB TPI type-name/type-size resolution now marks a record incomplete
  when depth-exhaustion (a cyclic or very deep pointer/modifier/array
  chain) substitutes a placeholder** — `type_name()`/`type_size()`'s own
  `depth > 10` guard previously did so without recording it in
  `unresolved_type_ref_count()`, unlike every other placeholder-
  substitution site in the same resolver.
- **`analysis_assurance`'s debug-evidence-state vocabulary now recognizes
  `not_supported`** — a BTF/CTF-sourced side's advanced channel legitimately
  reports this state (neither format carries calling-convention/value-ABI/
  frame-register facts at all), but it was missing from the closed
  `known_states` set, so every such comparison added a false "debug
  evidence was only presence-probed or failed to parse" note even when the
  basic channel parsed cleanly.
- **`tests/check_stripped_fp.py`'s reduced-evidence downgrade guard no
  longer rejects a case whose `expected_kinds` mix an L0 kind (never
  DWARF-dependent) with a DWARF-dependent one** — the guard required every
  kind to name a debug channel before allowing a BREAKING→clean downgrade
  as expected evidence loss; an L0 kind like `runtime_floor_raised` has no
  debug channel at all (by design, not because coverage was lost), which
  previously poisoned the whole channel set and rejected a genuine
  downgrade as an unproven regression.

