---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Local-exec TLS in an AArch64 shared object leaves no binary evidence

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** Records a toolchain limitation: local-exec TLS in an AArch64 shared object leaves no binary evidence. tests/test_elf_static_tls.py asserts the fact stays False.


`has_static_tls` is read from `DF_STATIC_TLS` *or* a TP-relative dynamic
relocation (`extract/elf_static_tls.py`), which covers initial-exec on every
architecture, including AArch64, where GNU ld writes no `DF_STATIC_TLS`. One
case stays invisible: `-ftls-model=local-exec` in a **shared object** on
AArch64. GNU ld (binutils 2.42) links it with the TP offset fixed at link time,
emitting neither a TLS dynamic relocation nor the flag, so nothing in the
binary records the requirement (`tests/test_elf_static_tls.py` asserts the
fact stays `False` there rather than fabricating a positive). Local-exec is
only valid for executables, so this is toolchain misuse rather than a
supported configuration. Closing it would need instruction-level evidence
(the `tprel` relocations in the object files, i.e. L3 build evidence).
