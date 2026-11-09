---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Without headers or DWARF a Rust cdylib run reports `NO_CHANGE` with `Gate: ACCEPTED`, and header-less additive changes get alignment noise (2026-10-15)

**Status (2026-10-15): OPEN.** Two related behaviors on the no-header path; both are generic ELF behavior that Rust hits by default.

**1. Headline label.** A default `cargo build --release` (no debug info) compared without headers:

```
Compatibility: NO_CHANGE | Policy: strict_abi | Gate: ACCEPTED (exit 0)
WARNING: Compatibility is UNCONFIRMED ...
WARNING: Binary-only analysis without debug info; many ABI changes cannot be detected
```

The run had a real return-type change. The report carries `analysis_assurance: partial` and `evidence_tier: elf_only`, but the headline label is a clean `NO_CHANGE` and the gate accepts it. The two warnings say the opposite. The AGENTS.md rule is that weaker evidence narrows conclusions and never upgrades to a clean compatibility claim, so this label is a candidate violation. Changing it is a verdict-labelling decision (ADR-066/063), not a fix for one reader.

**2. Alignment noise (fixed 2026-11-09).** Adding an exported function to a Rust cdylib, with no headers, produced `exported_object_alignment_reduced` on an unrelated exported static (`RL_VERSION`, 64 to 32 bytes) and `COMPATIBLE_WITH_RISK`, because the check derives alignment from the symbol's link-time address and a neighbour's addition moves it. The finding is now kept but demoted to `COMPATIBLE` (`policy/address_alignment_bound.py`) unless the old side declares an alignment the new address fails; see the [archive](../archive/known-gaps-closed.md).

Fix direction: the headline should not read `NO_CHANGE` while the surface is unconfirmed.

Workaround: pass headers (cbindgen) and build with `debug = 2`. See [Rust Libraries](../../use/rust-libraries.md).

Index: [Known gaps](../known-gaps.md)
