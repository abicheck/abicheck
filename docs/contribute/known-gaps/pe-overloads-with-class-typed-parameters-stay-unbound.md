---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# PE: overloads with class-typed parameters stay unbound across MSVC and Itanium manglings

Index: [Known gaps](../known-gaps.md)

**Status (recorded 2026-11-09): NARROWED.** Coverage gap, not a false verdict.

## Evidence

A header backend spells each declaration's linker name for its own target.
castxml, and clang on a non-Windows host, write Itanium names
(`_ZN2ns1fEi`). An MSVC DLL exports Microsoft C++ ABI names
(`?f@ns@@YAHH@Z`). Before 2026-11-09 those never met: every C++ declaration
read as unexported. When nothing matched at all, the parsed header surface
was discarded (`mangling-fallback`). See the bug class
`extraction.header_surface_discarded_on_export_mismatch`.

`extract/header_export_binding.py` now binds across the schemes by the
qualified name both manglings encode. Same-named overloads are separated by
the parameter list decoded from the MSVC name, which covers builtin
parameter types only. What stays unbound:

- an overload set whose parameters are pointers, references, class or enum
  types, or templates;
- an MSVC export whose qualified name no parsed declaration has, such as a
  template instantiation, an operator, a constructor or destructor, or a
  symbol in an anonymous namespace (`msvc_scope_components` returns `None`
  for these).

Unbound exports are compared from the PE export table only, as before. The
dump warns with their count, and the PE export delta still reports their
removal. A uniquely named declaration binds without a type check unless both
parameter lists decode.

## Intended resolution

Decode the full MSVC type grammar (pointers, references, cv-qualifiers,
class and enum names, back-references) into the same canonical spellings the
header backends produce. Alternatively, run the clang header backend with
`--target=<arch>-pc-windows-msvc` when the binary is an MSVC DLL. That needs
the MSVC standard headers on the parsing host.

Canary: `tests/test_header_export_binding.py::test_undecodable_overloads_stay_unbound_and_are_reported`.
