### Fixed

- **`bundle_intra_dep_removed` no longer reports symbols no bundle member ever
  exported.** The kind asserts a *diff-confirmed* removal -- it is `BREAKING`,
  and its description asserts that runtime load will fail -- but the OLD-side
  provider evidence that both claims rest on (`ever_provided_in_bundle`) was
  consulted only inside an allow-list branch gated on a non-empty
  `extra_needed`. A library carrying **zero** `DT_NEEDED` entries therefore
  never reached it, and every always-external import fell through to the
  symbol-name allow-list. Measured on conda-forge Intel MKL 2026.0.0 ->
  2026.1.0 (ground truth: +1 symbol, -0): 293 findings for `fflush`,
  `sincos`, `MPI_Finalize` and other libc/libm/MPI imports, turning a
  compatible release into exit 4, with no configuration able to suppress
  them (`bundle.system_providers` matches sonames, and the list was empty).
  The precondition is now a guard on emission: a symbol with no
  version-compatible in-bundle provider in OLD is suppressed when the outward
  `DT_NEEDED` evidence or the symbol-name allow-list accounts for it, and
  otherwise reported as `bundle_unresolved_intra_dependency`
  (`COMPATIBLE_WITH_RISK`), the kind that claims no diff confirmation. The
  shared half of the same bug -- reading an *empty* outward-edge list as
  evidence *against* externality -- is now one tested primitive,
  `bundle_detector_heuristics.extra_needed_all_system`, which both detectors
  share while each keeps its own emptiness policy.
- **Pre-existing cross-source hygiene debt no longer downgrades the verdict.**
  A hygiene finding stamped `CrossSourceEvolution.PERSISTENT` is present on
  *both* sides, so this release changed nothing about it, but every such
  finding still contributed `COMPATIBLE_WITH_RISK` to the pairwise verdict --
  39,956 `exported_not_public` findings, 39,955 of them persistent,
  downgraded an MKL release whose ground truth is +1 symbol / -0. Persistent
  findings are now excluded from the verdict at
  `checker._compute_verdict_for`, the single chokepoint every recompute
  routes through. Scoped to findings that resolve to
  `COMPATIBLE_WITH_RISK`: a persistent `odr_type_variant` or
  `header_build_context_mismatch` names a real defect in the candidate and
  still drives the verdict, as does any kind a policy deliberately promoted.
  The findings themselves are unchanged -- still reported, still tagged, still
  counted in `cross_source_evolution`.
- **An addition visible only in the export table is reported as an addition.**
  A header-aware snapshot builds its function map from the header AST, so an
  exported symbol that no public header declares never entered it and the
  old/new function diff could not see it appear: the same MKL release
  reported `Additions (1)` at `--depth binary` and `Additions (0)` with
  `-H`, with the new export reachable only as an `exported_not_public`
  hygiene finding -- a different claim, which drives neither the addition
  count nor the MINOR-bump recommendation. A new `func_added_elf_only`
  (`COMPATIBLE`, an addition) now reports it, the compatible-addition
  counterpart of `func_removed_elf_only`. Deliberately one-directional: a
  *removal* asserted from export-table evidence alone, for a symbol the
  headers never promised, is exactly the unproven finding `vision.md`
  forbids.
- **A per-kind rollup keeps one flooding kind from hiding every other
  finding.** A non-gating section contributing more than 25 findings of one
  kind is now summarised -- count plus a few example symbols -- instead of
  itemised, in the Markdown report only. The MKL comparison's ~120,000-line
  report was ~40,000 lines of one kind. Machine output is untouched and still
  carries every finding.

### Added

- **`compat check` reads real ABICC descriptors.** Four gaps closed, each of
  which made a real ABICC pipeline's descriptors unusable:
  ABICC's rootless descriptor *fragments* (sibling `<version>`/`<headers>`/
  `<libs>` with no wrapper root -- what real pipelines generate) now parse,
  retried once wrapped in a synthetic root, still through `defusedxml`;
  **directory** values for `<headers>` and `<libs>` are expanded to the
  headers and shared objects beneath them, which is ABICC's ordinary usage and
  previously reached the header parser as `#include "<dir>"` and the binary
  parser as "Unrecognised binary format"; a descriptor naming **multiple
  libraries** now has them paired across the two sides (by filename, then by
  version-insensitive stem so a SONAME bump still pairs) and compared,
  instead of comparing `libs[0]` and warning about the rest -- a library
  present on only one side is a coverage warning, never a removal, since a
  `<libs>` list is a selection and not an inventory; and the narrowing
  elements `<skip_headers>`, `<skip_including>`, `<skip_namespaces>`,
  `<skip_symbols>`, `<skip_types>`, `<skip_constants>` plus the compile
  elements `<include_paths>`, `<add_include_paths>`, `<defines>` and
  `<gcc_options>` are now parsed and applied. Every other descriptor element
  produces a warning naming it rather than being silently dropped: a declared
  rule with no effect, on a run that still reports a confident verdict, is
  worse than one that is rejected.
- **`dump`/`compare --exclude-header PATTERN`** excludes headers matching an
  fnmatch-style pattern (bare name, full path, or glob) from the parsed
  surface. Without it a header *directory* operand is all-or-nothing: a
  library whose public include tree contains two headers that cannot be
  parsed in the same translation unit -- two vendored copies of a third-party
  API declaring conflicting typedefs, as MKL's `include/` does for FFTW2 and
  FFTW3 -- made `-H include/` fail outright, with no flag, config key or
  descriptor element anywhere able to rescue it. Cache-correct without any
  cache-key change: both the AST and whole-snapshot keys already hash the
  resolved header list, and a run with no pattern passes its list through
  untouched, so no warm cache entry is invalidated.
- **A note when a plain snapshot is large.** `--compression auto` infers the
  storage envelope from the output suffix, so a `.json` name stays
  uncompressed however large it grows -- correct, and undiscoverable at the
  ~384 MB per side a real MKL dump reaches. `dump` now points at
  `.json.zst`/`.json.gz` once a plain snapshot passes 64 MB.
