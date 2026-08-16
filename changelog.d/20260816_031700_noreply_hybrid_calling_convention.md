### Fixed

- **Hybrid header calling-convention recovery** — preserve Clang `ms_abi` and `sysv_abi` declarations when CastXML omits them, so header-backed comparisons report ABI convention changes.
