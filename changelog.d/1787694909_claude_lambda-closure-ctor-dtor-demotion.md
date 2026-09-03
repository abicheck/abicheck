### Fixed

- **A destructor/constructor of a template instantiated over a local lambda
  closure type was never demoted from BREAKING even when confirmed
  unreachable from any consumer**, because
  `demote_lambda_closure_unexported_findings` (added to demote a
  `FUNC_PARAMS_CHANGED`/`TEMPLATE_PARAM_TYPE_CHANGED`/
  `TEMPLATE_RETURN_TYPE_CHANGED` finding whose
  reported symbol is confirmed absent from both binaries' real exported
  symbol table) deliberately excluded every castxml-synthesized ctor/dtor
  key — such a key is never itself a real exported symbol, so checking it
  against the export tables was vacuous. Reported against real oneTBB
  2021.13.0 → 2022.3.0 binaries: 5 breaking `func_removed` findings on
  synthetic ctor/dtor keys naming `tbb::detail::raii_guard<(lambda:...)>`/
  `try_call_proxy<...>`/`task_arena_function<...>`/`delegated_function<...>`,
  each paired with a compatible `func_added` differing only in the lambda's
  source line. Fixed by checking the *owning class/class-template* for
  export under any instantiation at all (a dependency-free Itanium
  `<source-name>` substring match against the raw exported names, no
  external demangler invoked) rather than the synthetic key text itself: a
  template with zero exported members under any instantiation, on either
  side, is now demoted (`Verdict.COMPATIBLE_WITH_RISK`,
  `modulation_rule="lambda_closure_never_exported"` — annotated, never
  removed, per this codebase's usual ADR-025 modulation hook); a template
  that does export some other instantiation is left exactly as severe as
  the detector made it, since that check cannot rule out the specific
  closure-parameterized instantiation was the one a consumer actually
  linked against. Six `std::` names (`allocator`, `basic_string`,
  `basic_istream`, `basic_ostream`, `basic_iostream`) carry a fixed,
  mandatory Itanium ABI substitution instead of their literal source-name
  in every real mangled symbol, so the check also matches that
  substitution (e.g. `Sa` for `std::allocator`) — without it, a synthetic
  `std::allocator<(lambda:...)>` finding would always read as unexported
  and demote even when genuinely removed.
