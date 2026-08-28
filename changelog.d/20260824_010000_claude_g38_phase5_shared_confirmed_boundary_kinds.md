<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: `calling_convention_changed`/`func_variadic_added`/
  `func_variadic_removed` on a bundled provider's export now correctly
  promote to a consumer-attributed `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED`
  bundle finding, without also fabricating one for `noexcept`/exception-spec/
  virtual-ness changes.** `bundle._detect_intra_dep_signature_changed`
  previously promoted only `func_params_changed`/`func_return_changed`/
  `var_type_changed` — so a confirmed `calling_convention_changed` on a
  provider's export correctly suppressed
  `bundle_signature_evidence`'s "couldn't tell either way"
  `BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` finding but was never itself
  promoted to a cross-library break, silently losing the consumer-attributed
  causality bundle reports exist to surface. Fixed by widening the
  promotion set to also cover `func_variadic_added`/`removed` and
  `calling_convention_changed` — every one a genuine, `BREAKING`, direct
  call-boundary mismatch. `func_noexcept_added`/`removed`,
  `func_exception_spec_changed`, `func_ref_qual_changed`, and
  `func_virtual_added`/`removed` remain deliberately excluded from
  promotion (though they still correctly suppress the "unverified"
  finding, a separate and weaker bar): `func_noexcept_added` has
  `default_verdict=COMPATIBLE` and the other three are either
  `COMPATIBLE_WITH_RISK` (explicitly "not a binary break") or describe
  vtable-slot layout rather than a mismatched calling boundary, so
  promoting any of them would fabricate a release-blocking bundle finding
  out of a change that is not one.
