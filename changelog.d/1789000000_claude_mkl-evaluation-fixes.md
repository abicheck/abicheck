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
  (`COMPATIBLE`, an addition), and `var_added_elf_only` for an undeclared
  data export, which is lost the same way, now report it, the compatible-addition
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
  single-library report, which are unchanged. A descriptor entry that never
  paired now lowers the merged `confidence` and `analysis_assurance.status`
  as well as appending a coverage warning, so a machine consumer cannot read
  a partial release comparison as fully covered.
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
- **Per-finding `library` in a multi-library report, and a `finding_id` that
  respects it.** Report schema **4.6** declares the optional `library` key a
  multi-library `compat` run already emitted, and `report_finding_id` folds
  it in when present. Two paired DSOs in one release routinely produce
  findings whose every identity field is equal -- a shared symbol removed
  from both -- so they hashed to one id and a consumer indexing the report
  by id kept one and silently dropped the other. A scalar comparison leaves
  the field unset, so its ids are byte-for-byte unchanged.
- **The release-wide assurance roll-up no longer overstates coverage.** Each
  axis now has an explicit worst-last scale ordered by how much assurance
  its label asserts, replacing a lexicographic pick over the non-best
  values. Merging `graph_completeness` `"degraded"` with `"not_collected"`
  returned `"degraded"`, asserting release-wide that a source graph *was*
  collected when one member collected none; `"asymmetric"` beat
  `"not_evaluated"` the same accidental way. Every member's own notes are
  still unioned into the merged block, so the specific detected defect is
  reported even when the one-word label defers to the least-claiming member.
- **An asymmetric `--exclude-header` is refused, not merely warned about.**
  Excluding a header from one side only never becomes a finding. ADR-050's
  `scope_fingerprint` already refused most of this shape -- the exclusion
  narrows the `-H` list before the contract is computed -- but it cannot
  refuse a baseline carrying no contract at all, and that case reported every
  declaration the excluded header carried as `func_removed`, verdict
  `BREAKING`, exit `4`. The patterns are now compared directly
  (`extract.header_exclusions.exclusion_asymmetry_reason`, a fourth
  comparability check beside the `dependency_scope` one), as sets, with no
  pre-v47 ambiguity carve-out needed: the flag and the field arrived
  together, so an older snapshot's empty value is a certainty rather than an
  unknown. `--diagnostic-comparison` remains the one way through.
- **`compat dump` reads the same descriptors `compat check` does.** A
  `<headers>`/`<libs>` *directory* operand -- ABICC's ordinary usage -- was
  expanded only on the `compat check` loading path, so the identical
  descriptor failed under `compat dump`: the library directory reached the
  binary parser as "Unrecognised binary format", the header directory
  reached the header parser as `#include "<dir>"`. Both consumers now expand
  at the same boundary.
- **The policy trail survives a multi-library merge.** `merge_results`
  dropped every per-library `disposition_ledger`, so report generation
  rebuilt one from the merged buckets: totals recovered, but every rule,
  reason, reclassification and acknowledgment match lost, and a release whose
  policy demonstrably acted reported an empty trail. The ledgers are
  concatenated instead (`policy/disposition_merge.py`), each record stamped
  with the DSO it came from, and deliberately not deduplicated across
  libraries -- two DSOs suppressing the same symbol under the same rule are
  two real dispositions. `acknowledgments`, `suppression_audit` and
  `unacknowledged_additions_review` stay dropped, now for three stated
  reasons rather than one blanket one.
- **A gating report section is never rolled up.** "Applied only to non-gating
  sections" was enforced by which sections called the rollup, which stopped
  being true the moment a severity setting made one of them gate: under the
  strict preset `potential_breaking`/`quality_issues` resolve to `error` and
  drive the exit code, yet were still collapsed to a count and five samples,
  so the Markdown report could not name every finding that blocked the run.
  The rule now lives in the function that states it.
- **Per-finding `library` reaches every report projection.** It was carried
  by JSON and itemized Markdown but not by the default HTML report or the
  compat XML's `<problem>`/`<name>` elements, so two paired DSOs' identical
  findings were indistinguishable in the projection most readers open. Added
  as a row and an attribute respectively, both omitted entirely for a scalar
  comparison.
- **`compat dump` applies the whole descriptor, not just its directories.**
  Expanding `<headers>`/`<libs>` was half the wiring: the dump still received
  the CLI's `gcc_options` and the unfiltered header list, so a descriptor
  relying on `<include_paths>`, `<defines>`, `<gcc_options>`, `<skip_headers>`
  or `<skip_including>` dumped a *different surface* than the identical
  descriptor under `compat check` -- which breaks the dump-then-compare
  workflow at its root, since the two sides are then not the same contract.
- **A descriptor value may contain a space.** The parser whitespace-split
  every element on a stated invariant the input does not obey: a Windows SDK
  include path under `C:\Program Files` became two nonexistent paths, and a
  `<defines>` value with whitespace two corrupted macros. Value elements now
  split on lines; `<gcc_options>` alone keeps the shell-like grammar, where
  whitespace tokenization genuinely is the grammar. The emitted flag string
  is quoted to match (`_compiler_options.join_gcc_options`, the inverse of
  `split_gcc_options`) -- parsing a spaced path correctly buys nothing if its
  own consumer re-breaks it one layer down.
- **A symlinked library is resolved, not dropped.** Excluding symlinks was a
  shortcut for collapsing a SONAME chain, and it silently dropped what an SDK
  actually ships: an overlay directory of links into a store elsewhere, where
  every selected library vanished -- a symlink-only directory reported
  "contains no shared libraries" and a mixed one compared part of the
  selected set without saying so. Deduplication is now by resolved target, so
  the chain still collapses to one entry.
- **A symbol that changes ELF type is not an addition.** The undeclared-export
  detector subtracted OLD's exports *within each symbol class*, so a name
  going `STT_OBJECT` -> `STT_FUNC` was absent from the function-only old set
  and read as newly gained: `func_added_elf_only` was emitted alongside the
  `symbol_type_changed` that already described the real change, corrupting the
  addition count and, through it, `-warn-newsym` and `recommend_release`'s
  MINOR bump. Every name OLD exported is now subtracted first, in either
  class; NEW's own type still decides which kind a genuinely new name gets.
- **A descriptor's `<skip_headers>`/`<skip_including>` are recorded like
  `--exclude-header`.** They narrow the parsed surface identically, but were
  filtered without being recorded, so the comparability check saw two empty
  exclusion sets. Against a stored operand carrying no contract -- an older
  snapshot or an imported ABICC dump, where `scope_fingerprint` cannot refuse
  either -- the comparison proceeded and declarations omitted from NEW came
  back as removals with a breaking verdict. The recorder moved to
  `model/header_exclusion_record.py` in the process: three layers produce a
  narrowed snapshot, and `compat/cli.py` is a `frontends` module that may not
  import `extract`.
- **A weak import that becomes strong is a new requirement.** The
  bundle-import history predicate matched on library and version only, so a
  *weak* OLD import counted as evidence that something outside the bundle
  provided the symbol. It is not: the loader resolves an unresolved weak
  symbol to `0`/`NULL`, so OLD loading proves nothing. The new strong import
  was then suppressed on the vacuously-satisfied outward-edge rule, and a
  candidate that now fails to load could report `NO_CHANGE`. The OLD match
  must now be non-weak; the callers already skip a weak NEW consumer.
- **`<gcc_options>` is parsed with the shared splitter.** A plain
  `str.split()` broke a shell-valid quoted argument (`-I"/opt/Program
  Files/inc"`) into fragments with quote characters still attached, which
  `_descriptor_compile_options` then quoted again -- so the compiler received
  nonexistent paths and malformed defines. It was also the one place that
  disagreed with `join_gcc_options`, the emitter the same values pass
  through: parser and emitter had two grammars for one string, and now share
  `split_gcc_options` on POSIX and Windows alike.
- **A descriptor skip and a native exclusion are no longer conflated.** The
  two are matched by different rules -- a descriptor's
  `<skip_headers>`/`<skip_including>` by exact basename or path, native
  `--exclude-header` by `fnmatch` -- so the same text names two different
  scopes: `*.h` excludes every header natively and nothing at all through a
  descriptor. Both wrote the raw text into `excluded_header_patterns`, so the
  comparability gate read two entirely different achieved surfaces as equal
  and could report fabricated additions or removals. Only what the matching
  rule could achieve is recorded now, and a wildcard in a descriptor skip is
  reported as ineffective rather than silently doing nothing. Whether real
  ABICC globs there is an open parity question (`docs/contribute/known-gaps.md`).
- **The exclusion gate and its warning no longer contradict each other.** The
  comparability check compared exclusion *sets* while the coverage warning
  compared *tuples*, so a native run's CLI order against a compat path's
  sorted record was accepted as comparable and then reported as having
  differing exclusions whose findings might be scope artefacts -- a false
  reduced-confidence diagnostic on a pair the tool had just declared
  comparable. Both now call one predicate
  (`model.header_exclusion_record.exclusions_are_symmetric`), and the message
  renders its patterns sorted, for the same reason the comparison ignores
  order.
- **The header-exclusion matching rule is recorded, not guessed from the
  pattern** (`AbiSnapshot.excluded_header_matching`, schema v48). Native
  `--exclude-header` is `fnmatch` and additionally tries `*/<pattern>`, so
  `include/foo.h` excludes `/pkg/include/foo.h`; a descriptor's
  `<skip_headers>` is exact basename-or-path membership and keeps it. Two
  successive attempts to decide comparability from the pattern *text* were
  falsified -- recording the raw text for both rules, then dropping only
  metacharacter-bearing patterns, which is wrong for any pattern containing a
  path separator. Two sides are now comparable only if they narrowed by the
  same patterns under the same rule. A pre-v48 snapshot loads as `"glob"`,
  correct for every one of them since the native path was the only producer.
  The cost is accepted deliberately: a descriptor and a native run naming a
  bare `b.h` do achieve the same thing and are refused anyway, because
  nothing in the patterns alone proves which pairs are equivalent.
