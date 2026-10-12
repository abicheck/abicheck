<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Lambda-parameterized functions no longer churn on an unrelated closure.** A
  non-constructor function whose parameter or return type is parameterized over a
  local lambda (`raii_guard<(lambda:lib.h#1)>`) reported `func_params_changed`,
  `func_return_changed` or their template equivalents when an unrelated closure was
  added earlier in the same header, which renumbers the lambda's per-header ordinal
  from `#1` to `#2`. The linker symbol is unchanged, so such a finding is now dropped
  when the old and new spellings differ only in closure discriminators and the
  function is exported under the same real Itanium key on both sides. An export-table
  change, a changed closure encoding inside the function, or an unexported symbol
  still reports as before.
