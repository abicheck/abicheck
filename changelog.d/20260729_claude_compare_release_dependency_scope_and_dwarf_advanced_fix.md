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
  can't always recover a genuine mangled name for an ordinary C++ function
  (e.g. a header auto-detected as plain C, or an uninstantiated C++ template;
  a known, already-documented limitation of `Function.mangled`'s
  `mangled == name` fallback), so a perfectly ordinary, non-dependency C++
  function could silently lose its own real DWARF finding (e.g. a
  `value_abi_trait_changed` calling-convention break) once dependency
  filtering actually ran. The filter now instead drops every excluded
  (dependency-header) function's own `mangled` spelling directly, kept or
  not — the bare-name ambiguity above only ever affects whether that
  spelling can be *trusted to identify* an entry it wasn't derived from, not
  whether an excluded function's *own* entry should be dropped (a genuinely
  unmangled C/`extern "C"` symbol's bare spelling is also its real linker-level
  name, so it still matches DWARF's key; a follow-up review round caught an
  over-correction that required a confident mangling marker for exclusion
  too, which left this class of dependency noise unfiltered again).
- **Two more `include_dependencies` propagation gaps, both Codex review**:
  `resolve_input()`'s recursive call following a GNU ld linker script to its
  real target dropped the flag back to its default (`True`), so filtering
  silently stopped for any operand that happened to be a linker script
  instead of the DSO directly. And `service.run_dump`'s public wrapper
  hid its own `include_dependencies` keyword from `inspect.signature()`
  (via `functools.wraps` following `__wrapped__` back to the unwrapped,
  parameter-less inner function) — invisible to the generated Python API
  reference and to any signature-driven caller. The wrapper now carries an
  explicit `__signature__` that includes the parameter.
- **`scan --against`/`--baseline` no longer hard-fails against a plain
  `dump`-produced baseline** (Codex review): `dump`'s default output is
  dependency-scope `"filtered"`, but `scan`'s own candidate and native-
  baseline `resolve_input()` calls left `include_dependencies` at its
  `True`/`"full"` default — so comparing a freshly scanned candidate against
  an ordinary, default-settings `dump` baseline hit the new comparability
  gate's `ScopeMismatchError` on the single most common `scan --against`
  workflow. Both calls now default to filtered too, matching `dump`/`compare`.
- **Two more real gaps, both Codex review, fresh evidence after the DWARF-
  advanced-metadata fix above**: (1) trusting an excluded (dependency)
  function's bare `mangled == name` spelling to exclude a DWARF-advanced
  entry could wrongly drop a *kept* function's own entry when a kept
  `extern "C" foo` and an unrelated excluded C++ dependency function (whose
  own real, different mangled name a header-AST backend failed to recover)
  happen to share that same bare spelling — no ODR conflict at the linker
  level, just an unreliable-spelling collision. An excluded mangled spelling
  that also names a kept function is no longer trusted to exclude anything.
  (2) `contract_coverage` only reflected a mixed-presence profile/scope
  fingerprint pair, not a mixed `None`/tagged `dependency_scope` pair — the
  comparability gate deliberately *permits* comparing a genuinely-untagged
  (pre-v18) snapshot against an explicitly-tagged one (there's no way to
  recover which mode the untagged side used), but that left the report
  reading as fully verified even though this axis was never actually
  checked. `contract_coverage` now reports `"partial"` for a mixed
  `dependency_scope` pair too.
- **`compare --dump-manifest`'s own project-owned roots weren't reaching the
  default dependency filter** (Codex review): `--dump-manifest` is mutually
  exclusive with `-H`, so `service.run_dump`'s wrapper only recovering
  `headers` from the bound call left it with no root set at all for a
  manifest-driven dump, falling back to a bare system-path heuristic that
  could misclassify the manifest's own `roots`/public-header
  paths/dirs/project-owned TU includes as dependencies (potentially hiding
  ABI breaks or emptying the declared surface) whenever they happened to
  sit under a system-like prefix. `dump`'s own path
  (`cli_dump_helpers.py`) already derived these via a
  `_dump_manifest_header_roots` helper; moved it to `dumper_scoping.py` as
  the shared `dump_manifest_header_roots` and wired it into the wrapper too.
