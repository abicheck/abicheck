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
- **DWARF type resolution now marks a struct/union incomplete when a
  standard type tag with no dedicated `_compute_type_info()` branch (e.g.
  `DW_TAG_ptr_to_member_type`, which typically carries no `DW_AT_name`)
  falls through to `_compute_fallback_type_info()`** — that fallback
  substituted the same placeholder shape (`tag`-derived name, byte-size-only
  fallback) as every other unresolved-type site without touching the
  completeness accumulator, so a member typed this way (e.g. `int A::*`)
  could mask a real ABI-relevant layout change behind an `evidence_state`
  that still read "parsed".
- `examples/ground_truth.json`'s case15 (`noexcept` frame-register-change
  detection) `known_gap` now also covers GCC, not just Clang: GCC's own
  `rsp`→`rbp` frame-register switch for the affected function only occurs
  at genuine `-O0`, not `-Og`/`-O2`/`-O3` — which is exactly what this
  catalog's CMake-driven `release-headers`/`build-source` artifact variants
  build with (`CMAKE_BUILD_TYPE=Release` implies `-O3 -DNDEBUG`). Clang
  never performs the switch at any tested optimization level (already
  covered). The ground-truth schema has no way to express an OR of two
  independent (toolchain, variant) conditions in one entry, so
  `known_gap_toolchains` is widened unscoped by variant — deliberately
  less precise than this case's sibling entries, accepted since
  `known_gap_observed` still pins the exact wrong verdict this covers.
- **The `mutmut` (detector core) PR lane's own "narrow scope to the diff"
  step could fail the lane outright on a diff whose implicated
  `only_mutate` module(s) didn't include `diff_symbols.py`/
  `checker_policy.py`** — `mutation_scope.py` rewrites the real, on-disk
  `pyproject.toml`'s `only_mutate` array in place before `mutmut run`
  (by design, so `mutmut` doesn't generate mutants for untouched modules),
  and `mutmut` always copies that already-narrowed file into `mutants/`
  before running the "not slow" suite there — including
  `tests/test_mutation_run_scoping.py::
  test_load_only_mutate_globs_reads_the_real_pyproject_toml`, whose "reads
  the real config" premise only holds when nothing upstream rewrote it.
  `-x` meant this aborted the whole lane before a single mutant was
  measured. Deselected from the mutmut lane the same way this repo's other
  repository-structure-reading tests already are (`pyproject.toml`'s
  `[tool.mutmut].pytest_add_cli_args`); it asserts against parsed config
  data rather than `only_mutate` module behavior, so it costs no
  mutation-kill coverage either way.

