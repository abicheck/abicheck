<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **G38 Phase 4's C-boundary signature-evidence gate is now wired into the
  real `compare --release`/bundle-analysis CLI path.**
  `find_unverified_signature_findings()` previously had no caller outside
  its own test module. `compare --release` (bundle analysis runs by
  default; `--no-bundle-analysis` opts out) now also captures each
  library's *new*-side `AbiSnapshot` alongside the already-stashed
  old-side one (`_compare_one_library`'s `collect_diff_results` gate, now
  triggered whenever bundle analysis is enabled, not only for
  `--bundle-facts-out`/JUnit), threads both maps through
  `_collect_bundle_result`/`_run_bundle_analysis` keyed by each library's
  bundle-canonical name, and folds
  `BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` findings into the same
  `bundle_findings` list the pre-existing, already-generic
  `bundle_findings` → JSON/Markdown rendering (`BundleFinding.to_change()`,
  `render_bundle_findings_markdown()`) already renders — no reporter
  changes were needed. Accepted tradeoff: since bundle analysis is
  enabled by default, this also means both sides' `AbiSnapshot`s are now
  held in memory for the whole release, not only the old side — the same
  memory-conscious gate this module's own docstring already documents,
  now paying that cost for every default release compare rather than
  only `--bundle-facts-out`/`--format junit`.

### Fixed

- **`bundle_signature_evidence.find_unverified_signature_findings()` could
  emit a spurious `BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` finding alongside
  an already-confirmed, `BREAKING` `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED`
  finding for a normally-versioned library** (CodeRabbit review, caught
  once this Phase 4 detector gained a real caller in the `compare
  --release` path). `DiffResult.library` is always the raw on-disk
  filename (`path.name`), which for a real versioned SONAME (e.g.
  `libfoo.so.1.2.3`) differs from the bundle-canonical key
  (`libfoo.so`, `binary_utils._canonical_library_key`) the resolution
  graph itself keys providers by — so the "a confirmed signature change
  already exists, don't also report it as merely unverified" precedence
  check never matched for any realistically-versioned library. Fixed by
  resolving each `DiffResult`'s basename back to its bundle-canonical key
  via a new `_basename_to_bundle_key()` helper (built from the bundle's
  own `old.libraries` mapping) before comparing. The function's own
  signature gained a leading `old: BundleSnapshot` parameter for this.
- **`bundle_signature_evidence._type_spelling_is_unresolved()` missed a
  wrapped form of the recursion-depth-cap sentinel** (Codex review). A
  parser's type-resolution recursion cap emits the bare `"..."` sentinel,
  but a pointer/reference wrapper one level up (`pdb_parser.py`,
  `dwarf_snapshot.py`) then wraps it into `"... *"`/`"... &"`/`"... &&"` --
  the exact-equality check (`spelling == "..."`) missed these composite
  forms, so a symbol whose evidence was genuinely insufficient could read
  as sufficient. Fixed by switching to a substring check on `"..."`, the
  same way the existing `"?"` sentinel is already checked.
- **`find_unverified_signature_findings()` did not restrict a provider's
  affected consumers to ones that can actually reach it** (Codex review).
  A bare `consumers_of(symbol)` lookup is name-only and set-wide -- two
  unrelated libraries can each export a same-named symbol without either
  being loadable together with a given consumer, the same limitation
  `bundle._detect_unresolved_intra_dependency`'s own docstring already
  documents for its own naive alternative. Fixed by restricting
  `consumer_libs` to consumers with a real `DT_NEEDED` path to the
  provider, using a new shared leaf module,
  `abicheck/bundle_resolution_reachability.py` (the `DT_NEEDED`-BFS
  primitive extracted out of `bundle.py`, which both modules now import --
  `bundle.py` re-imports it under its original private name so none of
  its own call sites needed to change; extracting it also dropped
  `bundle.py` from exactly the AI-readiness 2000-line hard cap to 1975).
  Deliberately narrower than `_detect_unresolved_intra_dependency`'s full
  contract: symbol-version/default-binding matching is not attempted here,
  documented as a remaining, narrower gap in the module's own docstring.
- **`_type_spelling_is_unresolved()`'s substring check on `"..."` was
  unsafe, unlike the sibling check on `"?"`** (Codex review, fresh
  evidence). A real, complete C/C++ type spelling can legitimately
  contain the literal substring `"..."` -- a variadic function-pointer
  parameter type like `"void (*)(int, ...)"` is fully-resolved evidence,
  not a truncated one, but the blanket substring check misclassified it
  as insufficient. Fixed by matching only the recursion-depth-cap
  sentinel's own finite shape (the bare sentinel, optionally followed by
  one or more ` *`/` &`/` &&` wrapper suffixes for nested pointer/
  reference wrapping, anchored at both ends) via a regex, rather than a
  substring check.
- **`_symbol_evidence_sufficient()` treated unknown variadicness
  (`Function.is_variadic is None`) as sufficient evidence** (Codex
  review, fresh evidence). `diff_symbols._check_variadic_change()` itself
  skips (`skip_none=True`) whenever either side's value is unknown -- an
  older snapshot/dumper that never populated the field is indistinguishable
  from one that positively determined "not variadic" -- so a real
  fixed-arity/variadic transition landing on an unknown side previously
  produced neither a confirmed diff-level finding nor this module's own
  risk finding: total silence on a real, calling-ABI-relevant unknown.
  Fixed by also requiring `is_variadic is not None` for a function's
  evidence to count as sufficient.
