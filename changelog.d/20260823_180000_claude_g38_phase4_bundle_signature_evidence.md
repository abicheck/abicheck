<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **C-boundary signature-evidence gate (G38 Phase 4)** — new
  `abicheck.bundle_signature_evidence` module:
  `find_unverified_signature_findings()` walks a bundle's resolution graph
  and, for each consumer/provider symbol pair that resolves by C linkage,
  checks whether the provider's own snapshot actually carries corroborated
  signature evidence (real DWARF/header type information, not just a bare
  ELF export) for that exact symbol on both sides of the comparison. When it
  doesn't — and no diff-level `func_params_changed`/`func_return_changed`/
  `var_type_changed` finding already confirmed a real change for that
  symbol — it emits the new `ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED`
  (`bundle_intra_dep_signature_unverified`, default verdict `RISK`): the
  binary-name-compatible-but-unconfirmed case distinct from both "no
  change" and the existing, confirmed `BREAKING`
  `bundle_intra_dep_signature_changed`. Like Phase 3's
  `bundle_multibuild`, this is a standalone companion module — not wired
  into `bundle.compare_bundle()` itself, since `abicheck/bundle.py` is at
  the AI-readiness 2000-line hard cap — a caller invokes it separately and
  merges its findings into `compare_bundle()`'s own.
