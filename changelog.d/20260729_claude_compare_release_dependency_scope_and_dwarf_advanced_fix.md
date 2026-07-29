<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`--include-dependencies` now reaches directory/package (`compare-release`)
  comparisons too** (Codex review on #651): the new default dependency-scope
  filtering for `compare`'s live-binary dumping was wired into the single-pair
  path only — a directory/package `compare` fanned out through
  `_dispatch_release_compare` -> `_compare_release_libraries` ->
  `_run_compare_pair` -> `service.run_compare` without ever forwarding the
  flag, so the same library pair could produce different findings compared
  directly versus through a release directory. `include_dependencies` is now
  threaded through the entire release-comparison engine (including the
  JUnit/annotations re-run pass in `_collect_release_extras`) and
  `compare-release`'s own `--include-dependencies` flag, so both comparison
  paths filter (or don't) identically.
- **Fixed a real DWARF-advanced-metadata filtering bug surfaced by the above
  default-filtering change**: `dumper_scoping.py`'s dependency-header filter
  used to keep a DWARF-advanced entry (`value_abi_traits`,
  `calling_conventions`, ...) only when its linkage-mangled symbol matched a
  *kept* header-AST function's own `mangled` field — but a header-AST backend
  can't always recover a genuine mangled name (e.g. a header auto-detected as
  plain C, or an uninstantiated C++ template; a known, already-documented
  limitation of `Function.mangled`'s `mangled == name` fallback), so a
  perfectly ordinary, non-dependency C++ function could silently lose its own
  real DWARF finding (e.g. a `value_abi_trait_changed` calling-convention
  break) once dependency filtering actually ran. The filter now instead drops
  only symbols *confidently* identified as belonging to an excluded
  (dependency-header) function — one whose mangled spelling differs from its
  bare name or carries a recognizable Itanium/MSVC mangling marker — keeping
  everything else by default, matching this module's existing
  false-negative-over-false-positive design bias.
