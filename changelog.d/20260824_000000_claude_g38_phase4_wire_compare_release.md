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
  one or more space-prefixed `*`/`&`/`&&` wrapper suffixes for nested
  pointer/reference wrapping, anchored at both ends) via a regex, rather
  than a substring check.
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
- **`_symbol_evidence_sufficient()` also treated unknown calling-convention
  attributes (`Function.contract_attributes is None`) as sufficient
  evidence** (Codex review, fresh evidence) -- the identical shape as the
  `is_variadic` gap above, for a different tri-state field
  (`list[str] | None`; calling-convention attributes such as `stdcall`/
  `ms_abi`/`vectorcall`). `diff_symbols._check_contract_attributes_change()`
  itself skips whenever either side is `None`, so a real calling-convention
  transition landing on an unknown side previously produced neither a
  confirmed diff-level finding nor this module's own risk finding. Fixed
  by also requiring `contract_attributes is not None`.
- **The recursion-sentinel regex missed two more real composite forms,
  and a wholly separate, unconditional placeholder** (Codex review,
  fresh evidence). `pdb_parser.py`'s qualifier wrapping renders the
  depth-capped sentinel with a *prefix*, not a suffix (`"const ..."`),
  and its array wrapping appends `"[]"` (`"...[]"`, possibly further
  wrapped, e.g. `"...[] *"`) -- neither matched the previous regex.
  Separately, `dwarf_snapshot.py`'s `DW_TAG_subroutine_type` handling and
  `pdb_parser.py`'s procedure/member-function branches both render *any*
  function/subroutine type as the fixed literal `"fn(...)"`,
  unconditionally -- never the real return/parameter types, regardless
  of recursion depth -- which the sentinel-only regex could never match
  by construction. Fixed by widening the regex to accept an optional
  `const `/`volatile ` prefix and `[]` among the suffix forms, and by
  separately recognizing the exact `"fn(...)"` literal.
- **`find_unverified_signature_findings()` never checked symbol-version/
  default-binding compatibility before pairing a consumer with a
  provider** (Codex review, fresh evidence). `consumers_of(symbol)`
  matches by bare name only, so a consumer requiring `foo@V2` could still
  pair with a `ProviderEntry` whose only definition is `foo@V1` -- a
  provider that cannot actually satisfy that consumer at all (a real
  resolution failure, already covered by
  `BUNDLE_UNRESOLVED_INTRA_DEPENDENCY`/`BUNDLE_INTRA_DEP_REMOVED`, not a
  signature-mismatch risk this module exists to flag). Fixed by adding a
  new `_consumer_matches_provider()` predicate, mirroring
  `bundle._detect_unresolved_intra_dependency`'s own version/
  `version_soname`/`is_default` compatibility rules, evaluated per
  (consumer, provider) pair.
- **`_CONFIRMED_SIGNATURE_CHANGE_KINDS` didn't include the two change
  kinds this module's own new `is_variadic`/`contract_attributes`
  sufficiency checks introduced positive counterparts for** (Codex
  review, fresh evidence). A symbol with a real, diff-confirmed
  `FUNC_VARIADIC_ADDED`/`FUNC_VARIADIC_REMOVED`/`CALLING_CONVENTION_
  CHANGED` that also happened to carry an unrelated unresolved field
  (an unresolved parameter type, say) still produced a redundant,
  contradictory "cannot be confirmed or denied" risk finding alongside
  the already-proven break. Fixed by adding all three kinds to the
  confirmed-kinds set.
- **`find_unverified_signature_findings()` treated any old-side symbol
  sharing a provider's bare name as "the same export retained across the
  release," even when it was a different GNU symbol version** (Codex
  review, fresh evidence). When a provider previously exported only
  `foo@V1` and the new release adds `foo@V2` for a consumer requiring
  V2, the old-side check (`_symbol_was_exported`) only ever reads
  `AbiSnapshot.function_map`/`variable_map` -- both keyed by bare name,
  with no per-version distinction -- so it answered "yes, `foo` was
  exported" purely from the unrelated `foo@V1` entry, and the detector
  reported V2 as a retained-signature risk even though V2 has no old-side
  counterpart to compare against at all. Fixed by adding
  `_provider_entry_retained_from_old()`, which checks
  `old.resolution.provides[symbol]` (the bundle-resolution layer, which
  *is* version-aware) for a same-library, same-version `ProviderEntry`
  before treating the new provider entry as retained.
- **The `mutmut (detector core)` CI lane could no longer run at all once
  this PR touched a second mutation-scoped module
  (`bundle_signature_evidence.py`)** -- a real, already-known failure
  class (see the two pre-existing `pyproject.toml` `pytest_add_cli_args`
  entries this joins), not new to this PR's own code:
  `tests/test_action_run_sh_compare_pr_json_write.py`'s
  `TestRealAbicheckWritesPersistedAnnotationsForADirectoryOperand` shells
  out to a real `abicheck compare` via `action/run.sh` from a scratch
  `cwd`, and mutmut's PYTHONPATH-based redirection into `mutants/` follows
  that subprocess into a tree whose config loader can't find
  `pyproject.toml`'s `[tool.mutmut]` section, aborting the whole `-x`
  lane before a single mutant is measured. Fixed by adding this test file
  to the same `--ignore=` list the two prior instances of this failure
  already use, with a matching `_ACCEPTED_KILL_LOSS` entry in
  `tests/test_mutation_workflow_contract.py` recording the real, non-zero
  mutation-kill coverage this exclusion gives up (per that test's own
  "an exclusion is free only if the file reaches no mutated module"
  contract).
- **`_CONFIRMED_SIGNATURE_CHANGE_KINDS` still omitted most `Function`-level
  facts `diff_symbols.py` can confirm independently of `return_type`/
  `params`/`is_variadic`/`contract_attributes`** (Codex review, fresh
  evidence). A real, diff-confirmed `FUNC_NOEXCEPT_ADDED` (or seven other
  kinds: `FUNC_NOEXCEPT_REMOVED`/`FUNC_EXCEPTION_SPEC_CHANGED`/
  `FUNC_REF_QUAL_CHANGED`/`FUNC_VIRTUAL_ADDED`/`FUNC_VIRTUAL_REMOVED`/
  `CTOR_EXPLICIT_ADDED`/`CTOR_EXPLICIT_REMOVED`) on a symbol that also
  happened to carry an unrelated unresolved field (an unresolved parameter
  type, say) still produced a redundant, contradictory "cannot be
  confirmed or denied" risk finding alongside the already-proven change.
  Fixed by adding all eight kinds to the confirmed-kinds set, with the
  module's own docstring now explaining which kinds are deliberately
  excluded (`FUNC_LANGUAGE_LINKAGE_CHANGED`, since it changes the mangled
  name itself; vtable-slot/inline-transition kinds, which are layout facts
  rather than calling-signature-agreement facts) and why.
- **`find_unverified_signature_findings()` evaluated evidence sufficiency
  against a version-blind `AbiSnapshot.function_map`/`variable_map` entry
  even when a provider retained multiple live GNU versions of the same
  bare symbol name** (Codex review, fresh evidence). `AbiSnapshot` keeps
  exactly one `Function`/`Variable` entry per bare name, so when a
  provider exports both `foo@V1` and `foo@@V2` (an ordinary shape for a
  library that has never broken ABI compatibility), that single entry
  cannot be attributed to either version specifically — a consumer
  requiring V1 could be told evidence was fully sufficient purely because
  the collapsed entry happened to look complete, even though no
  V1-specific signature was ever actually captured. Fixed by adding
  `_bare_name_version_collapsed()`, which detects the collapse via the
  bundle-resolution layer's own per-version `ProviderEntry` list (which
  `AbiSnapshot` does not carry) and fails evidence sufficiency closed
  when detected, so the "unverified" finding correctly fires instead of
  silently trusting ambiguous evidence.
- **The version-collapse fix above did not close the identical gap in the
  earlier "a confirmed change already exists" precedence check, which ran
  before it** (Codex review, fresh evidence). `find_unverified_signature_
  findings()`'s main loop checked `(provider_lib, symbol) in confirmed`
  and skipped the provider entry entirely before ever reaching the new
  `_bare_name_version_collapsed()` guard -- so when a provider retained
  both `foo@V1` and `foo@@V2` and a diff-confirmed change landed on the
  bare-name symbol `foo` (itself only ever describing whichever version
  the model's own bare-name collapse happened to keep), *both* provider
  entries were silently suppressed, dropping the unverified finding for
  whichever consumer's version the confirmed diff did not actually cover.
  Fixed by computing the version-collapse condition once per
  `provider_entry` up front and only honoring the confirmed-precedence
  skip when the bare name is not collapsed. Two regression tests: one
  with two consumers pinned to each of two collapsed versions (confirmed
  to fail against the pre-fix code, which produced zero findings for
  either), one confirming precedence is unaffected for an ordinary,
  non-collapsed provider.
- **`_type_spelling_is_unresolved()` did not recognize `dwarf_snapshot.py`'s
  own fallback placeholder for an unsupported DWARF type-DIE tag** (Codex
  review, fresh evidence). `_compute_type_name`'s fallback branch (reached
  for a tag with no dedicated handling, e.g. `DW_TAG_ptr_to_member_type`)
  returns `name or tag or "unknown"` -- when the DIE carries no
  `DW_AT_name`, this leaks either the bare literal `"unknown"` or the raw,
  unresolved DWARF tag spelling itself as though it were a real type name,
  and (via the same wrapping layer the recursion-depth-cap sentinel
  already accounts for) can appear composited with pointer/reference/array
  suffixes and qualifier prefixes too. Fixed by widening the existing
  recursion-cap regex (renamed `_UNRESOLVED_WRAPPED_SENTINEL_RE`) to also
  match the bare `"unknown"` literal and any `DW_TAG_\w+`-shaped spelling,
  wrapped the identical way. Four new parametrized regression cases,
  confirmed to fail against the pre-fix regex.
- **`find_unverified_signature_findings()`'s retained-export check was a
  uniform, per-`ProviderEntry` fact, but retention is not actually uniform
  across consumers when a symbol's default binding changes** (Codex
  review, fresh evidence). When a provider previously exported only
  `foo@V1` (`is_default=False`) and the new release marks the identical
  version `foo@@V1` as default, an unversioned consumer -- which binds
  only to a default definition, per `_consumer_matches_provider`'s own
  rule -- could not have resolved `foo` from this provider in the old
  release at all; for that consumer specifically, the new binding is a
  genuinely new capability, not a retained edge whose signature could
  have silently changed. The existing `_provider_entry_retained_from_old`
  check (version-string-only) still correctly reported this provider
  entry as retained overall, so an "unverified" finding could fire for a
  consumer with no old-side counterpart to compare against. Fixed by
  adding `_consumer_retained_from_old()`, evaluated per (consumer,
  provider) pair in the `consumer_libs` filter rather than folded into
  the existing per-provider-entry check -- the two answer genuinely
  different questions and both must hold (a consumer requiring the
  specific version `V1` explicitly is unaffected either way, since its
  own match rule never inspects `is_default`, and still gets a finding).
  Deliberately not implemented as "add `is_default` to the existing
  per-provider check," which would have been strictly wrong in the
  other direction: it would also suppress the finding for a version-
  specific consumer that genuinely could reach the old, non-default
  definition. Regression test with two consumers (unversioned,
  version-specific) confirmed to fail against the pre-fix code.
- **Declined: retaining every library's full old+new `AbiSnapshot` for the
  whole release comparison, now that bundle analysis is enabled by
  default** (Codex review, fresh evidence expanding on this PR's own
  already-documented tradeoff -- see this fragment's "Added" section
  above). `_old_snapshot` cannot simply be shrunk to a compact
  evidence-only structure: it is shared with a pre-existing JUnit
  rendering path (`cli_compare_release.py`'s `collect_diff_results`
  handling, predating this PR) that requires a real `AbiSnapshot`
  (`isinstance(old_snap, AbiSnapshot)`-gated). A safe fix needs the
  stashing code to know *why* `collect_diff_results` was triggered (JUnit
  vs. bundle-analysis-only vs. both) and store a full snapshot only when
  JUnit needs one -- a genuine, if narrow, control-flow change to
  already-reviewed, working code, not a same-pass patch under continued
  review pressure. Left as a known, accepted gap per this file's own
  "known gaps over risky reactive patches" convention.
