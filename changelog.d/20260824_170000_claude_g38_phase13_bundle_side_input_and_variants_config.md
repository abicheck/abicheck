<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **G38 Phase 13: a unified live/stored bundle-comparison pipeline, and the
  first real caller of `pair_variants()`.**

  `abicheck/bundle_side_input.py` — `LiveBundleInput`/`StoredBundleFactsInput`
  (a `BundleSideInput` union) and `resolve_bundle_side()` give live and
  stored bundle sides one shared resolution step (`(BundleSnapshot,
  {canonical_name: AbiSnapshot | BundleSignatureEvidence},
  InstantiationManifest | None)`), instead of `_run_bundle_analysis` (live)
  and `bundle_facts.compare_bundle_from_facts()` (stored) each computing
  the identical shape independently. `compare_bundle_sides()` is the one
  comparison entry point built on it, able to express every pairing —
  live/live, stored/live, live/stored, stored/stored — all routed through
  Phase 12's `bundle_analysis.analyze_bundle()` orchestrator so none can
  independently drift on which detectors ran.

  `compare_release_against_bundle_facts()` gives `compare_bundle_from_facts()`
  a real end-to-end driver for the first time: given a stored OLD-side
  `BundleFacts` path and a live NEW-side directory, it discovers the NEW
  side's `.so` files, dumps and diffs each matched library through the
  Tier-2 `service.resolve_input`/`service.compare_snapshots` chokepoints,
  builds the NEW side's compact `BundleSignatureEvidence` projection (Phase
  9's memory discipline), and populates a real `new_signature_evidence` map
  — closing Phase 12's own "no end-to-end CLI invocation" gap at the
  Python-API level.

  `abicheck/bundle_variants_config.py` — `parse_bundle_variants_config()`
  (eager, hard-error validation of a `bundle_variants:` mapping into
  `BundleVariantSpec` objects: `target_triple`/`compiler_family`/
  `feature_toggles`/`required`) and `run_bundle_variant_pairing()`, the
  first real caller of `bundle_multibuild.pair_variants()` anywhere in this
  codebase outside its own test suite. A missing `required: true` variant's
  `BUNDLE_VARIANT_COVERAGE_REGRESSED` finding is escalated to
  `Verdict.BREAKING` via the existing ADR-027 D3.2
  `BundleFinding.effective_verdict` override mechanism, rather than a
  second, parallel gating path.

  **Deliberately not wired to a CLI flag or `.abicheck.yml` discovery**:
  every file that would host that dispatch (`cli_compare_release.py`,
  `cli_compare_helpers.py`, `cli.py`, `cli_options.py`,
  `buildsource/inline.py`) is within two lines of the AI-readiness
  2000-line hard cap, or already at it — see the G38 plan doc's Phase 13
  "Known gap" section for the measured line counts and what a correct fix
  needs (splitting one of those files first, its own dedicated pass).
