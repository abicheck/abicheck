### Changed

- **ADR-061 continuation**: 2 more root-level `abicheck/*.py` modules are
  now classified into the `compare` responsibility layer in
  `architecture/modules.yaml` (47 of the layer's `legacy_paths` total, up
  from the pre-existing 45): `finding_identity_atomic.py` (a split-out
  sibling of the already-classified `finding_identity.py` -- old/new
  symbol-identity canonicalization, importing only `model`), and
  `versioned_symbol_scheme.py` (the ICU-style versioned-symbol-name
  matcher that folds `foo_75`/`foo_78` into one identity across old/new).
  Verified via `scripts/check_architecture.py`
  (0 errors, no new dependency cycle), `scripts/check_ai_readiness.py` (0
  errors), `mypy abicheck/` (17 errors, unchanged from the documented
  yaml-stub-only baseline), `scripts/adr_status_sync.py` (clean), and the
  full fast unit suite.

  **`finding_identity_ctor_dtor.py` was also initially classified
  `compare` in this batch and reverted after a second Codex review
  finding, real and confirmed by reading the file directly.** It imports
  `SYNTHETIC_CTOR_KEY_PREFIX`/`_SYNTHETIC_DTOR_KEY_PREFIX`/
  `is_synthetic_ctor_key`/`is_synthetic_dtor_key` from `.dumper_castxml`
  directly -- unclassified at the time this PR opened, so no violation
  fired, but the *parallel* dumper-cluster PR in this same ADR-061 batch
  was actively classifying `dumper_castxml.py` as `extract` at the same
  time. Landing both would have created the identical cross-PR
  `compare -> extract` collision the `type_metadata.py` revert below
  already describes -- confirmed by reading the import directly rather
  than trusting the original "importing only `model`" claim, which was
  wrong for this file (right only for its sibling,
  `finding_identity_atomic.py`, which genuinely imports only
  `name_classification.py`, itself `model`-classified). Reverted to
  unclassified; the shared synthetic-ctor/dtor-key primitives belong with
  `dumper_castxml.py`'s own eventual `extract` classification (or `model`,
  if split out) in a follow-up.

  **`versioned_symbol_scheme.py`'s own "no local imports at all" claim
  was also wrong, per a third Codex review finding, real and confirmed by
  reading the file.** It imported `ChangeKind` from `checker_policy.py`
  (currently unclassified, so no live violation, but genuinely policy-
  shaped and the eventual target of a future `policy` classification per
  PR #913's own investigation) rather than from `ChangeKind`'s canonical
  home, `model.change_catalog.kinds` (moved there by PR #902's model
  split -- `checker_policy.py`'s own `ChangeKind` is an unchanged
  re-export, `from .model.change_catalog.kinds import ChangeKind as
  ChangeKind`). Unlike the two reverts above, this one had no live
  cross-PR collision to force a revert -- fixed the import itself
  instead, pointing directly at the canonical, already-`model`-classified
  source rather than routing through an unclassified re-export.
  Behavior-identical (same class, same object identity), verified via
  `mypy abicheck/versioned_symbol_scheme.py` (clean) and
  `pytest -k versioned_symbol` (60 passed, 1 skipped).

  **`type_metadata.py` was initially classified `compare` in this same
  batch and reverted after a Codex review finding, real and confirmed by
  reading the file directly.** Its own docstring states its purpose
  plainly: "Unified `TypeMetadataSource` protocol for all debug format
  readers... so the checker's detectors can consume type information
  without knowing the source format" -- an extraction-layer protocol/
  schema, not an old/new matcher. Concretely, `btf_metadata.py` and
  `ctf_metadata.py` -- both classified `extract` by the parallel
  `extract`-utilities PR in this same ADR-061 batch -- import `FuncProto`/
  `read_null_terminated_string` from it directly. Landing both PRs would
  have created a real `extract -> compare` violation the moment they
  merged, invisible to either PR's own `check_architecture.py` run in
  isolation since each ran against its own pre-merge base and had no
  visibility into the other's sibling PR's classification choice.
  Reverted to unclassified; `FuncProto`/the `TypeMetadataSource` protocol
  belongs with `extract` (or `model`, if split, per the Codex finding's own
  suggestion) in a follow-up, not force-classified here on a second guess.

  **Most of the assigned candidate set was investigated and left
  unclassified, each for a documented reason** -- this batch was
  deliberately conservative rather than forcing a classification through:

  - **`checker.py`** -- read in full per the task's own framing as a
    judgment call. It is genuinely workflow-shaped, not compare-shaped: it
    imports `dwarf_advanced.py` (already `extract`-classified) directly for
    supplementary DWARF facts, plus `contract_pipeline.py`/`policy_file.py`
    (policy-layer concerns -- contract relevance, suppression) and
    `checker_policy.py` (unclassified, but conceptually policy's
    `Verdict`/`ChangeKind` machinery). `compare`'s `may_import: [model]`
    forbids the `extract` edge outright, so this is a structural blocker,
    not just a stylistic one -- confirming the "main comparison
    orchestrator" framing in the module map over a narrower "compare" read.

  - **`detectors.py`/`detector_registry.py`** -- individually clean
    (`detectors.py` has zero local imports; `detector_registry.py` imports
    only `detectors.py`), but `checker_types.py` (already `model`-classified,
    `may_import: []`) imports `DetectorResult` from `detectors.py` directly.
    Classifying `detectors.py` as `compare` would make `model -> compare`
    the very next `check_architecture.py` run flags, an inward-layer
    importing an outward one -- exactly backwards for ADR-061 D1's ring
    ordering. Left both unclassified together, since splitting the pair
    (registry classified, contract type not) would be an arbitrary
    inconsistency with no dependency-safety benefit.

  - **The `comparability*.py` cluster** (`comparability.py`,
    `comparability_fields.py`, `comparability_json.py`,
    `comparability_language_mode.py`, `comparability_sequences.py`) --
    the four siblings are individually import-clean (`model` and each
    other only), but `comparability.py` itself is imported by
    `cli_dump_helpers.py`, already `frontends`-classified
    (`may_import: [model, report, workflows]` -- no `compare`). Classifying
    `comparability.py` as `compare` would make that existing, real
    lazy-import (`compute_extraction_contract` inside a dry-run renderer)
    a `frontends -> compare` violation. Left the whole cluster unclassified
    rather than split the parent from its file-size-driven siblings (each
    sibling's own docstring states it was split out of `comparability.py`
    purely to stay under the 2000-line hard cap -- they are one module in
    every sense but the filesystem).

  - **`diff_numpy_capi.py`** -- imports `.numpy_capi`
    (`extract`-classified) only inside a `TYPE_CHECKING` block, which reads
    as harmless at runtime, but `check_architecture.py`'s AST-based import
    scanner does not special-case `TYPE_CHECKING` guards (confirmed by
    reading the scanner) -- it flags the edge exactly as it would an
    unconditional import. Left unclassified rather than special-casing the
    checker for one file's typing-only import, which is a change to shared
    verification infrastructure this PR's narrow scope doesn't own.

  - **`diff_cpp_patterns.py`, `diff_platform.py`,
    `diff_platform_elf_dynamic.py`, `diff_versioning.py`,
    `diff_wheel_deployment.py`** -- each imports `binary_utils.py`
    (already `extract`-classified) directly for symbol-name normalization
    (`strip_vendor_hash`). A direct `compare -> extract` violation in
    every case; none of the already-classified `compare` files import
    `binary_utils.py`, so this is a real, pre-existing dependency these
    five files carry that their siblings do not.

  - **`diff_python.py`** -- imports `python_ext.py`
    (`extract`-classified) for `PythonExtMetadata`. Same shape as the
    `binary_utils.py` group above.

  - **`diff_python_api.py`** -- imports `python_api.py`
    (`extract`-classified). Same shape.

  - **`diff_stdlib_impl.py`** -- imports `build_mode.py`
    (`extract`-classified) for `StdlibFamily`/`build_mode_from_signals`.
    Same shape.

  - **`surface.py`, `surface_graph.py`, `type_reachability.py`,
    `type_reachability_spelling.py`** -- individually import-clean (only
    `model` and each other, plus the already-`compare`-classified
    `diff_cxx_rules.py`), and already consumed by one existing `compare`
    file (`diff_types_surface.py`). Left unclassified on a role rather than
    an import-safety judgment: `surface.py`'s own docstring states its
    purpose as "classifies individual diff findings as in-surface (public)
    or out-of-surface (private / internal)" (ADR-024) -- that is a
    relevance/classification decision, which the task-routing table in
    `AGENTS.md` assigns to `policy` ("Decide relevance, suppression,
    classification, severity, or gating"), not `compare`'s "match
    old/new entities or identify a raw change." `type_reachability*.py`
    exists specifically to sharpen that same surface-relevance question
    (which `std::`-namespaced types are directly referenced vs. only
    transitively reachable) and `surface_graph.py` is `surface.py`'s own
    query-layer companion. A parallel agent working the `policy` slice of
    this same ADR-061 migration is the more consistent place for this
    cluster's classification decision, so it was left for that stream
    rather than pre-empted here.

  No file outside `architecture/modules.yaml` and this changelog fragment
  was touched -- no source-code changes were needed to land the four
  classifications above.
