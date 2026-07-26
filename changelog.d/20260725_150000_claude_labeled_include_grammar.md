<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`abicheck compare`'s `--include` gains a labeled
  `old:LABEL=PATH`/`new:LABEL=PATH`/`both:LABEL=PATH` form (ADR-050 D1).**
  A side-specific support root that owns no declared `--header` (a shared
  logical identity like `--include old:support=old/src --include
  new:support=new/src`) can now be told apart, order-sensitively, from every
  other declared `-I` slot when `comparability.compute_extraction_contract`
  builds `profile_fingerprint`'s per-slot token — instead of spuriously
  hard-failing `PROFILE_MISMATCH` on an otherwise ordinary two-checkout
  compare. `cli_params.SidedIncludePathParam` layers the labeled form on top
  of the existing `old=`/`new=`/`both=` grammar (the labeled colon spelling
  never reinterprets an existing, valid unlabeled value containing a literal
  `=`); `cli_options.split_sided_include_paths` collects the resolved
  `path -> label` map, threaded through `run_compare` down to
  `dumper.dump()`/`service.resolve_input` and into
  `comparability.IncludeDir.label`. A labeled `--include` is rejected
  up front (not silently dropped) on a directory/package (release) compare,
  which doesn't yet thread labels into its per-library fan-out.
  `cli_params.LabeledIncludePathParam` (the narrower, sides-free
  `both:LABEL=PATH` grammar `dump`'s single-input `--include` needs) is
  built but not yet wired into `dump_cmd`; `scan --against`'s own separate
  `--include` registration doesn't recognize the labeled form yet either —
  both are tracked as explicit follow-up in `comparability.py`'s module
  docstring, not silently dropped scope.

### Fixed

- **`service.py`'s PE/Mach-O header-scoped dump helper
  (`_try_header_scoped_dump`/`_has_matched_public_surface`) relocated to a
  new sibling module, `service_header_scoped.py`**, to free line budget for
  the labeled-`--include` threading above — `service.py` was at the
  AI-readiness file-size hard cap. Bound back into `service.py` via
  `importlib.import_module` (not a static `from .service_header_scoped
  import ...`) so the new leaf module isn't pulled into the pre-existing,
  already-baselined `cli_buildsource`/`scan_engine` import cycle the
  AI-readiness `import-cycle-growth` gate tracks — the same escape hatch
  `cli_buildsource.py`'s own back-compat re-export shim already documents.
  Pure relocation, no behavior change; `service._try_header_scoped_dump`/
  `service._has_matched_public_surface` (and every test that monkeypatches
  them by that name) keep working unchanged.
