### Changed

- `param_renamed` is now `COMPATIBLE_WITH_RISK` instead of `API_BREAK` in
  native `compare`. A parameter rename changes neither the binary ABI nor
  C/C++ source compatibility (neither language has named arguments); it stays
  reported for generated bindings, documentation and IDE tooling. `abicheck
  compat` keeps reporting it as a source-level problem for abi-compliance-checker
  parity (`compat._helpers.ABICC_SOURCE_LEVEL_KINDS`). Its `sdk_vendor`
  downgrade to `COMPATIBLE` is removed, since that policy only downgrades
  `API_BREAK` kinds; under `sdk_vendor` it is now a risk as well.
