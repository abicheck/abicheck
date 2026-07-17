# case188_public_class_private_base_class — Public class newly gains a private base class

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `public_api_internal_dependency_added` · **Evidence tier:** L5

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

In the derived L5 source graph the public class `demo::PublicHandle` newly
carries a `TYPE_INHERITS` edge to the internal (non-public-header) type
`detail::InternalBase` that it did not reach in v1 — a public class privately
inheriting from an internal base. The base class is not part of the field
layout consumers write to directly, but its behavior (virtual dispatch,
construction/destruction order, any data it contributes to the object) is now
silently coupled to `demo::PublicHandle`'s public contract.

This is the sibling of [case187](../case187_public_struct_private_field_type/README.md)
(private field type) and [case189](../case189_public_function_private_parameter_type/README.md)
(private parameter type) — three of the four non-call edge kinds
`type_graph.py` populates (ADR-041 P0 slice 1): `TYPE_INHERITS`,
`TYPE_HAS_FIELD_TYPE`, and `DECL_HAS_TYPE`, plus `DECL_REFERENCES_DECL`
([case190](../case190_public_inline_function_references_internal_constant/README.md))
for a non-call body reference.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | no exported symbol changed by this alone; a base-class addition may not even shift `sizeof` if the base is empty |
| Header AST (L2) | `demo::PublicHandle`'s own public members are unchanged; the header AST records the base class exists, but not that it is *internal* relative to the public surface |
| **Source graph (L5)** | the derived type-dependency delta (`TYPE_INHERITS`) → the finding |

## How to fix

Either promote `detail::InternalBase` to a documented part of the API, or
replace the inheritance relationship with composition/pimpl so the internal
type's evolution cannot silently change `demo::PublicHandle`'s behavior.
