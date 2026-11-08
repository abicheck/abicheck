---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Without headers or DWARF a Rust cdylib run reports `NO_CHANGE` with `Gate: ACCEPTED`, and header-less additive changes get alignment noise (2026-10-15)

**Status (2026-11-08): section 1 CLOSED by [ADR-090](../adr/090-unresolved-surface-is-not-a-clean-verdict.md); section 2 OPEN.** Two related behaviors on the no-header path; both are generic ELF behavior that Rust hits by default.

**1. Headline label (CLOSED 2026-11-08).** Such a run now reads `Compatibility: UNCONFIRMED | ... | Gate: REJECTED (exit 1)`: the JSON `verdict` and `run_outcome.compatibility` are `UNCONFIRMED`, `release_recommendation.version_bump` is `undetermined`, and `assurance.unconfirmed: warn` keeps the label at exit 0. The Rust cdylib pair itself was not re-run (no cargo here); the same no-header, no-debug ELF shape is covered by `tests/test_unconfirmed_compatibility_matrix.py`. The original report, kept for history: A default `cargo build --release` (no debug info) compared without headers:

```
Compatibility: NO_CHANGE | Policy: strict_abi | Gate: ACCEPTED (exit 0)
WARNING: Compatibility is UNCONFIRMED ...
WARNING: Binary-only analysis without debug info; many ABI changes cannot be detected
```

The run had a real return-type change. The report carries `analysis_assurance: partial` and `evidence_tier: elf_only`, but the headline label is a clean `NO_CHANGE` and the gate accepts it. The two warnings say the opposite. The AGENTS.md rule is that weaker evidence narrows conclusions and never upgrades to a clean compatibility claim, so this label is a candidate violation. Changing it is a verdict-labelling decision (ADR-066/063), not a fix for one reader.

**2. Alignment noise.** Adding an exported function to a Rust cdylib, with no headers, produced `exported_object_alignment_reduced` on an unrelated exported static (`RL_VERSION`, 64 to 32 bytes) and `COMPATIBLE_WITH_RISK`. The check (`diff_platform_elf_symbols._check_object_alignment_reduced`) derives alignment from the symbol's link-time address, so a neighbour's addition moves it. With headers the finding does not appear, because declared alignment corroborates or refutes it. The clang variant of the same gap is pinned in `tests/test_clang_header_backend_integration.py`.

Fix direction for the remaining section 2: the address-derived check needs declared-alignment evidence before it fires on a header-less run. Under ADR-090 a header-less run that carries this finding reads `UNCONFIRMED`; the finding stays in the report.

Workaround: pass headers (cbindgen) and build with `debug = 2`. See [Rust Libraries](../../use/rust-libraries.md).

Index: [Known gaps](../known-gaps.md)
