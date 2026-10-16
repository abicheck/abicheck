---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `debug = 1` DWARF has no function types, so a return-type break reads `NO_CHANGE` with `assurance: complete` (2026-10-15)

**Status (2026-10-15): OPEN.** Reproduced on a Rust cdylib; the cause is in the DWARF reader's input, not in the comparison.

Repro: a cdylib with `[profile.release] debug = 1` exporting `rl_add(i32, i32) -> i32`, compared with a variant returning `i64`, and no headers:

```
abicheck compare base/librl.so new/librl.so
# verdict NO_CHANGE, exit 0, assurance: complete, evidence_tier: dwarf_aware
```

The same pair built with `debug = 2` gives `BREAKING` (`func_return_changed`). `readelf --debug-dump=info` shows the `DW_TAG_subprogram` for `rl_add` at `debug = 1` has no `DW_AT_type` and no `DW_TAG_formal_parameter` children; at `debug = 2` both are present.

So the DWARF section is present, and the assurance model counts it as complete, but the exported functions carry no type information for the comparison to read. A clean `complete` label is therefore not earned.

Fix direction: a DWARF-based `complete` claim should require type information for exported subprograms, and report `partial` (with a reason) when an exported function has none. This changes assurance accounting (ADR-063/064), so it is recorded here rather than patched in the reader.

Rust impact: `debug = 1` is a common release setting. The documented workaround is `debug = 2` or cbindgen headers, see [Rust Libraries](../../use/rust-libraries.md).

Index: [Known gaps](../known-gaps.md)
