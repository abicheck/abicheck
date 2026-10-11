---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A lost return-pointee qualifier is judged by the C++ conversion rule, not C's stricter one (2026-10-11)

Index: [Known gaps — closed and moot](../archive/known-gaps-closed.md)

**Status (2026-10-11): CLOSED.** A C-callable function is now judged by the C rule; see the archive entry in [Known gaps — closed and moot](../archive/known-gaps-closed.md).

`func_return_pointee_qualifier_removed` (risk) versus
`func_return_pointee_qualifier_changed` (API break) is decided by whether the
new returned type still converts implicitly to the old one
(`compare/parameter_facts.py`'s `_qualification_converts`). That predicate
implements C++ [conv.qual]: a qualifier may be added below the first level
when every level above it carries `const`, so `char **` -> `const char *
const *` converts. C has no such rule: only the first pointee level may gain
a qualifier implicitly, and GCC 14+ rejects `const char * const *p =
(char **)q;` as `incompatible-pointer-types`.

So for a C consumer, `const char * const *f()` -> `char * const *f()` and
similar multi-level losses are reported as a risk while `const char * const
*p = f();` stops compiling. The C++ result is verified against g++ by
`tests/test_return_pointee_qualifier_compiler_integration.py`.

Closing it needs the source language of the consumer contract on the
comparison (a C header vs a C++ one), which the snapshot does not record per
function today; the parameter-side rule (`pointee_qualifier_changes`)
already uses the stricter, language-neutral first-level rule.
