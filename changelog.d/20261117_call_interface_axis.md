### Added

- **x86-64 System V call-interface evaluator (ADR-094 D8, not yet wired into verdicts).** `abicheck.compare.call_interface.assess_call_interface` decides whether an old binary still calls a function correctly after its signature changed. It reads each parameter's and the return's recorded ABI fact, and returns `preserved`, `broken` or `unknown` with a reason. A test checks it against the Lean-proved model of the formal fragments E13 and E14 on all 3336 cases, with no mismatches. No verdict or exit code changes yet: ADR-094's shadow comparison and switch come later.
