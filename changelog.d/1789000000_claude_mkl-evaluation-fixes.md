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
  `workflows.bundle_import_evidence.extra_needed_all_system`, which both
  detectors share while each keeps its own emptiness policy -- and a *newly
  introduced* unresolved import is never suppressed by it, because outward
  `DT_NEEDED` edges say nothing about an import that has no history: only an
  import OLD already carried unresolved is evidence that something outside
  the bundle provides it. Without that second question, a vendor-symbol typo
  in a new library would produce a clean bundle result while failing at load
  time.
- **Pre-existing cross-source hygiene debt no longer downgrades the verdict.**
  A hygiene finding stamped `CrossSourceEvolution.PERSISTENT` is present on
  *both* sides, so this release changed nothing about it, but every such
  finding still contributed `COMPATIBLE_WITH_RISK` to the pairwise verdict --
  39,956 `exported_not_public` findings, 39,955 of them persistent,
  downgraded an MKL release whose ground truth is +1 symbol / -0. Persistent
  findings are now excluded from the verdict at `checker._compute_verdict_for`
  (via `policy.persistent_hygiene.drop_persistent_hygiene`), the single
  chokepoint every recompute routes through. Scoped to findings that resolve to
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
  forbids. The new kind is registered with every addition consumer --
  `-warn-newsym`, `-strict`, the HTML/compat-XML added-symbol count and the
  longitudinal lifecycle -- and `func_removed_elf_only`, which was missing
  from the report classifier's own removal set all along, joins it. The
  detector is skipped only when the *new* side is headerless,
  since that is the one shape in which the ordinary function diff already
  reports the addition; keying it off the old side as well lost the addition
  in a mixed-evidence comparison, where neither map contains it.
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
  version-insensitive stem so a SONAME bump still pairs, on every platform's
  own convention -- macOS and Windows put the version *before* the
  extension) and compared, instead of comparing `libs[0]` and warning about
  the rest. A name that is ambiguous on either side -- the same basename
  under two directories, which is what an architecture split looks like after
  a recursive `<libs>` expansion -- is left unpaired rather than matched
  arbitrarily, so a run never compares one architecture against another. A
  library present on only one side is a coverage warning, never a removal, since a
  `<libs>` list is a selection and not an inventory. A multi-library
  descriptor set against a single *stored snapshot* is refused rather than
  answered from `libs[0]`: one snapshot is one library, and nothing in the
  inputs says which entry it corresponds to. A descriptor's own
  `<gcc_options>`/`<defines>`/`<include_paths>` are placed *before* any
  `-gcc-options` given on the command line, so the explicit flag wins the
  last-wins contest GCC actually runs. And the narrowing
  elements `<skip_headers>`, `<skip_including>`, `<skip_namespaces>`,
  `<skip_symbols>`, `<skip_types>`, `<skip_constants>` plus the compile
  elements `<include_paths>`, `<add_include_paths>`, `<defines>` and
  `<gcc_options>` are now parsed and applied. Every other descriptor element
  produces a warning naming it rather than being silently dropped: a declared
  rule with no effect, on a run that still reports a confident verdict, is
  worse than one that is rejected. A multi-library result's
  `analysis_assurance` block is a release-wide roll-up to the weakest
  member's value on each judgement axis, not dropped: dropping it made every
  reporter omit the block and stopped `--require-complete-analysis` gating on
  a member whose evidence was partial or failed. Every finding in a
  multi-library result names the library it came from (`library`, in JSON and
  Markdown), so a removal is attributable and the same symbol removed from
  two libraries stays two findings; the field is absent from every
  single-library report, which are unchanged.
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
  untouched, so no warm cache entry is invalidated. The patterns a snapshot
  was dumped under are recorded on it (`AbiSnapshot.excluded_header_patterns`,
  schema v47) and disclosed as a coverage warning, so a narrowed surface is
  never presented as a complete one -- and a stored baseline dumped under one
  set of exclusions is refused against a differently-scoped candidate by the
  existing ADR-050 `scope_fingerprint` check rather than silently reporting
  the asymmetry as change. A *loaded* snapshot keeps the patterns it was
  really dumped under -- this run parsed no headers for it, so this run's
  patterns say nothing about it. `--exclude-header` with `--dump-manifest`
  is rejected rather than silently ignored: a manifest dump parses the
  translation units the manifest declares, not the `-H` list the pattern
  narrows (see `docs/contribute/known-gaps.md`).
- **A note when a plain snapshot is large.** `--compression auto` infers the
  storage envelope from the output suffix, so a `.json` name stays
  uncompressed however large it grows -- correct, and undiscoverable at the
  ~384 MB per side a real MKL dump reaches. `dump` now points at
  `.json.zst`/`.json.gz` once a plain snapshot passes 64 MB.
