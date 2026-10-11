---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# AArch64 AAPCS64 by-value aggregate passing is not modeled

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/dwarf_advanced.py:885-893 models only SysV AMD64 (_SYSV_AMD64_RETURN_ARCHES), and its comment says an AArch64 HFA flip is just a generic value-ABI change.


A struct passed or returned by value on AArch64 travels in SIMD registers
when it is a homogeneous floating-point or short-vector aggregate (HFA/HVA,
1-4 members of one type), in general registers when it is at most 16
bytes, and indirectly otherwise (AAPCS64 §5.9.5). A change that moves an
aggregate across one of those boundaries without changing its size -- one
`float` member becoming an `int` -- breaks every compiled caller, and no
`ChangeKind` reports it: the value-ABI trait diff
(`dwarf_advanced._diff_value_abi_traits`) models only the SysV AMD64
register/indirect rule and treats an AArch64 trait flip as a generic
value-ABI change. `docs/reference/platforms.md` already lists HFA/HVA
drift as not detected.

A classifier for those boundaries
(`macho_metadata.classify_aapcs64_aggregate`) existed as an unwired,
unit-tested "modeling primitive"; the plan that was to wire it (G1) closed
without doing so, and the dead-code plan's Stage D removed it (it is in git
history, with its unit tests, `tests/test_macos_arm64_abi.py`). Wiring it is not a
Mach-O concern -- AAPCS64 governs AArch64 ELF too -- so a fix belongs in the
value-ABI trait path for `target_arch == "aarch64"`, needs member base
types from DWARF, and needs an AArch64 toolchain to validate against real
binaries, which this environment does not have.
