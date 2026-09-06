### Fixed

- **A failed compiler-identity probe no longer lets a mismatched GCC/Clang
  pair compare silently.** A compiler-probe failure (`dumper_toolchain`'s
  host-compiler resolution erroring, or — for a castxml-produced snapshot —
  falling back to guessing the toolchain family from castxml's own executable
  name) previously fed an *absent* `compiler_family` into the extraction
  contract, indistinguishable from a probe that was simply never attempted.
  Two independent probe failures could therefore fingerprint identically and
  compare as "comparable" even when the underlying host compilers genuinely
  differed. `ExtractionContract` now records an explicit
  `FactStatus.FAILED`/`PRESENT` toolchain-identity status
  (`extract.toolchain_identity.compiler_identity_status`), and
  `check_contracts_comparable` refuses the comparison outright
  (`ProfileMismatchError`, or the equivalent `ComparabilityMismatch` under
  `--diagnostic-comparison`) whenever either side's probe failed, independent
  of whether the opaque `compiler_family` fields happen to still agree.
- **A snapshot pair with no DWARF/DWARF-advanced evidence on either side now
  reports a genuine "layout unverified" row.** `analysis_assurance`'s
  `layout_unverified_detectors` names the registered layout-bearing
  detectors (`dwarf`, `advanced_dwarf`, `layout_descriptor`) that ran — or
  were skipped — with no debug-info evidence at all, so their own
  zero-finding result is no longer indistinguishable from "checked this
  pair's layout, found nothing."
