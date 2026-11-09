---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# PE: overloads with undecoded parameter types stay unbound across MSVC and Itanium manglings

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
qualified name both manglings encode, and only when the parameter lists
decode and agree (`extract/msvc_signature.py`: builtins, classes, structs,
unions and enums by value, one level of pointer or reference, and name and
parameter back-references). Binding never rests on a unique name alone,
because that would bind a function in a release with one overload and not
in a release with two. What stays unbound:

- a C++ function whose parameter list the decoder does not model: template
  specializations (`?$name@args@`), function pointers, arrays, multi-level
  pointers, pointers to members;
- an MSVC export whose qualified name no decoder models: an operator, a
  constructor or destructor, a template instantiation, or a symbol in an
  anonymous namespace (`msvc_scope_components` returns `None` for these).

Such an export is kept on the surface as an export-table entry with its
header declaration unknown, never read as undeclared, so its removal is
still reported. The snapshot records `export-binding-partial` (or
`export-binding-unresolved` when nothing bound) and the dump warns with the
count. Header-level checks of those declarations (parameter defaults,
deprecation) are not made on PE.

## Intended resolution

Decode the rest of the MSVC type grammar (template arguments, function
types, operator and structor names) into the canonical spellings the header
backends produce. Alternatively, run the clang header backend with
`--target=<arch>-pc-windows-msvc` when the binary is an MSVC DLL. That needs
the MSVC standard headers on the parsing host.

Canary: `tests/test_header_export_binding.py::test_undecodable_overloads_stay_unbound_and_are_reported`.
