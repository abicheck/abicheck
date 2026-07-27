<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: seven more loader/version/deployment-
  packaging findings are now `NOT_APPLICABLE`** (`contract_evaluation.py`;
  opt-in via `compare(..., contract_evaluation=True)`; no default-path
  behavior change): a binary-wide file/product version regression
  (`library_version_downgraded` -- PE's `VS_FIXEDFILEINFO` resource or
  Mach-O's `LC_ID_DYLIB` current_version) and a missing ELF version script
  (`version_script_missing`, synthetic `<version-script>` subject) are
  linker/version metadata, not a function/variable/type. DF_STATIC_TLS
  toggling (`static_tls_introduced`/`static_tls_removed`) is a binary-wide
  loader/TLS-model property, the same synthetic-subject shape as the
  PT_INTERP/DT_* loader-control kinds. The C++ standard floor
  (`cxx_standard_floor_raised`, `symbol="__cplusplus"`) and the NumPy C-API
  target floor (`numpy_target_floor_raised`, `symbol="<numpy-capi>"`) are
  the same synthesized, package-wide minimum-toolchain-floor shape as
  `runtime_floor_raised`/etc., just for a different axis. The Python
  stable-ABI floor (`python_abi3_floor_raised`) is the identical shape
  spelled with a real module symbol -- without an entry here it fell
  through to the `python_*`-prefix "trusted by construction" shortcut and
  was wrongly reported `IN_CONTRACT` instead of the mode-independent
  `NOT_APPLICABLE` every other deployment-floor kind already gets.
