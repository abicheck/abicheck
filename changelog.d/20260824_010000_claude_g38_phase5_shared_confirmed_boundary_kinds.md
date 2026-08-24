<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: the confirmed-signature-change kind set used to
  promote a cross-library break and the kind set used to suppress
  `BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` no longer disagree.**
  `bundle._detect_intra_dep_signature_changed` previously promoted only
  `func_params_changed`/`func_return_changed`/`var_type_changed` to a
  consumer-attributed `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` finding, while
  `bundle_signature_evidence.find_unverified_signature_findings`
  independently suppressed its own "unverified" finding on nine more kinds
  (`func_variadic_added`/`removed`, `calling_convention_changed`,
  `func_noexcept_added`/`removed`, `func_exception_spec_changed`,
  `func_ref_qual_changed`, `func_virtual_added`/`removed`). A confirmed
  `calling_convention_changed` on a provider's export therefore correctly
  suppressed the "couldn't tell either way" finding but was never itself
  promoted to a cross-library break, silently losing the consumer-attributed
  causality bundle reports exist to surface. Both detectors now import one
  shared `bundle_models.CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS` frozenset
  instead of maintaining two independent lists.
