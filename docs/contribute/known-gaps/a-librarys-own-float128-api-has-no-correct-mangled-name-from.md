---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A library's own `_Float128` API has no correct mangled name from castxml

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: castxml mangling was not run. The entry records the defect as an unresolved castxml limitation: _Float128 functions read as not exported.


Found while fixing castxml on binary128 `long double` targets (`extract/
castxml_header_compat.py`). The **type** spelling is right on every target
(`_Float128`), but castxml's `mangled` attribute for a function taking one is
not the Itanium name the compiler emits (`_Z2qfDF128_P1Q`): on x86-64 castxml
reports the bare name (`qf`), on AArch64 a name embedding the stand-in record
(`_Z2qf19__abicheck_Float128P1Q`). Either way the export join misses, so such a
function reads as not exported on both architectures. A token substitution is
not a fix (Itanium substitution numbering differs between a class name and a
builtin type), so it was not attempted. glibc's own `*f128` declarations are
system headers and unaffected.
