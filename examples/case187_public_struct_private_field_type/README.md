# case187_public_struct_private_field_type — Public struct newly gains a private field type

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `public_api_internal_dependency_added` · **Evidence tier:** L5

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

In the derived L5 source graph the public type `demo::Public` newly carries a
`TYPE_HAS_FIELD_TYPE` edge to the internal (non-public-header) type
`detail::PrivateType` that it did not reach in v1 — the ADR-041 headline
scenario, `struct Public { detail::PrivateType* p; };`. Nothing was *called*;
a public struct simply grew a field whose type is private. The public surface
has taken on an undeclared dependency, so a later change to the internal
type's layout or semantics becomes a hidden risk to the API.

This is the sibling of [case160](../case160_public_api_internal_dep_added/README.md),
which demonstrates the same `public_api_internal_dependency_added` finding via
a `DECL_CALLS_DECL` edge (a public function calling an internal one). Both
finding kinds are produced by the same detector
(`source_graph_findings._internal_dependency_findings`) over the shared
`DEPENDENCY_EDGE_KINDS` family — the point of this case is that the "reaches"
relationship is not calls-only: `TYPE_INHERITS` (a private base class),
`TYPE_HAS_FIELD_TYPE` (this case, a private field type), `DECL_HAS_TYPE` (a
private parameter/return type), and `DECL_REFERENCES_DECL` (a body reading an
internal constant) all count, because `type_graph.py`'s clang-AST pass
(ADR-041 P0 slice 1) populates the three non-call edge kinds the schema had
reserved since ADR-031 but nothing produced until this ADR.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | no exported symbol changed — a struct's field-type identity isn't part of the mangled/exported surface |
| Header AST (L2) | `demo::Public`'s own declaration is unchanged from the outside; the private field's *type* being newly internal isn't visible from `Public`'s public-header shape alone |
| **Source graph (L5)** | the derived type-dependency delta (`TYPE_HAS_FIELD_TYPE`) → the finding |

The field's declared type is internal — it has no public declaration of its
own — so a header-only or binary-only diff shows `demo::Public` unchanged.
Only the L5 type graph reveals the new structural dependency.

Per ADR-028 D3 a build/source-evidence finding never decides a shipped-ABI
break on its own — an artifact diff (e.g. a layout change proven by DWARF)
proves any concrete break; this finding flags the elevated risk and localizes
the cause for review.

## How to fix

Either promote `detail::PrivateType` to a documented part of the API, or keep
`demo::Public`'s field types independent of internals whose evolution
consumers cannot track (e.g. an opaque handle or a pimpl indirection instead
of embedding the internal type directly).
