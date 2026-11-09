---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Without clang, castxml's template-default fallback misses a macro-spelled default (2026-11-09)

**Status (2026-11-09): NARROWED.** What is left of "a header-only default template-argument change reads `NO_CHANGE` at L2 on castxml" (2026-10-21, closed 2026-11-09; see the [archive](../archive/known-gaps-closed.md)).

## What is fixed

castxml 0.7 emits no XML element for an uninstantiated class, function, alias or variable template. An instantiation keeps only the spelling the code wrote (`Box<char>`), so the default cannot be recovered from castxml's XML. Extracting it from castxml was rejected and stays rejected.

The castxml backend already parses the same headers with clang, to read `noexcept` and C++20 constraints (`extract/headers/castxml/clang_semantics_join.py`, extractor generation 45). Since generation 46 the same parse also records the defaults of every template: class, function, alias and variable templates, and type, non-type and template-template parameters (`extract/headers/clang/template_default_entries.py`). They are stored as `AbiSnapshot.cxx_template_defaults` (extension fact `abicheck.cxx_template_defaults`) and compared by `compare/template_default_table.py`. The clang backend records the same table from its own AST, which also closes its alias/variable-template gap: before, the clang backend had no model entity for those and only named a gap.

- The same header now gives the same answer on both backends: `template_param_default_changed` (`API_BREAK`). A default only added to a class, alias or variable template is not a break (case224). A function template's added default is reported.
- A reference to an earlier parameter (`U = T`) is spelled by position, so renaming a parameter is not a change. A member template's reference to an enclosing template's parameter is spelled by position too.
- A template whose default cannot be spelled (`sizeof(T)`), whose declarations disagree, or that cannot be keyed is recorded as known but unspelled. The last case is a member template of an explicit or partial specialization. That side reads `partial` with `template-parameter defaults (a default could not be spelled)`.
- When castxml's join cannot run, the table is not modelled. The run then reads `NO_CHANGE` with `partial` assurance and `template-parameter defaults (castxml cannot record them)` named, whenever the header text spells a default (ADR-085 D3).

Tests:

- `tests/test_template_default_header_backends.py`: a compiler oracle on built libraries for both backends, the no-clang path, the stored round-trip, specialization members and explicit instantiations.
- `tests/test_template_default_assurance_gap.py`: every family and parameter kind through the CLI on both backends.
- `tests/test_default_template_arg_l2_matrix.py`: shapes A, B and C (ADR-085).

## Residual

The no-clang fallback is the text scan in `extract/headers/template_default_scan.py`. It strips comments and strings and looks for a top-level `=` in a `template <...>` list. It does not expand macros, and it reads only the headers the parse was given. A default spelled through a macro (`template <class T, class U DFLT(int)>`), or declared in a header reached only by `#include`, is therefore not seen. Such a change reads `NO_CHANGE` with `complete` assurance on castxml when clang is missing. With clang, the AST reads the expansion and the change is reported.

Canary: `tests/test_template_default_header_backends.py::test_castxml_without_clang_misses_a_macro_spelled_default`.

A fix would run the preprocessor before the scan (`castxml -E` is available wherever castxml is), or name the gap for any castxml C++ run whose join did not run and whose headers spell `template`. The second fix is broader, but it would mark every template-using castxml run without clang as `partial`.

Index: [Known gaps](../known-gaps.md)
