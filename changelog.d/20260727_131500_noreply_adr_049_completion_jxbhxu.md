<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: remaining security-hardening and
  deployment-floor findings are now `NOT_APPLICABLE`**
  (`contract_evaluation.py`; no behavior change outside this still-unwired
  shadow module): `writable_executable_segment` (a W^X violation),
  `text_relocation_removed`, and CET/branch-protection changes
  (`cet_protection_weakened`/`_improved`, `branch_protection_weakened`/
  `_improved`) are the same binary-wide build-flag hardening posture as the
  security kinds already covered (RELRO/PIE/canary/FORTIFY/...), but were
  omitted from the original curation. Separately, `runtime_floor_raised`,
  `platform_baseline_floor_raised`, `macos_deployment_target_raised`,
  `x86_isa_baseline_raised`, and `os_deployment_floor_raised` are each a
  synthesized headline finding over a synthetic subject (e.g.
  `"libc.so.6:GLIBC_2"`) describing the minimum runtime/OS/CPU-ISA a binary
  now requires, never a specific function/variable/type a consumer's code
  references -- only the neighboring wheel-deployment checks were covered
  before this. All now short-circuit to `NOT_APPLICABLE` instead of falling
  through to header-surface classification.
