# case190_public_inline_function_references_internal_constant — Public inline function newly reads an internal constant

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `public_api_internal_dependency_added` · **Evidence tier:** L5

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).
>
> **This case cannot currently be proven with a real compiled example**,
> unlike its siblings [case187](../case187_public_struct_private_field_type/README.md)/
> [188](../case188_public_class_private_base_class/README.md)/
> [189](../case189_public_function_private_parameter_type/README.md), which
> were converted after direct verification. Two real, independent blockers,
> both confirmed empirically against this repository's current code (not
> theoretical):
>
> 1. **Header-only-graph path** (the L2 header-only graph, built
>    automatically since G29 Phase A; case191's mechanism):
>    `source_graph_findings._HEADER_FULL_VISIBILITY_KINDS` deliberately
>    excludes `DECL_REFERENCES_DECL`/`DECL_CALLS_DECL` from what a header-only
>    pass's "zero edges" can be trusted to mean — a header-only scan can only
>    see a body if it happens to live *in* a header (inline/template), so its
>    absence of a reference is not proof the whole project has none. This is
>    a deliberate, well-reasoned false-positive guard (see the docstring at
>    that name), not a bug, and it applies even when this exact scenario is
>    compiled for real: a live test built and run with two header-only-graph
>    sides still cannot get `DECL_REFERENCES_DECL` credited as "common"
>    between old/new, so `public_api_internal_dependency_added` never fires.
> 2. **Build-integrated path** (`dump --sources`/`--build-info`,
>    `type_graph.py`): classifying a type/decl as genuinely internal requires
>    either an explicit `private_header` visibility tag or `defined_in_project`
>    provenance, both of which trace back to `Target.private_headers` —
>    populated only by CMake's File API `FILE_SET` feature
>    (`adapters/cmake_file_api.py`). Nothing in this catalog's CMake macros
>    uses `FILE_SET` (nor does any other build adapter — Bazel/Ninja/Make
>    never populate `private_headers` either), so this path currently
>    classifies nothing as internal for *any* project that doesn't happen to
>    use that specific, rarely-adopted CMake feature.
>
> Closing this gap needs one of: CMake `FILE_SET` wiring in this catalog's
> build files plus File API querying in the validation harness, or a product
> fix teaching the build-integrated path the same "not in the public root
> set → private" fallback `header_graph.py` already uses. Tracked as
> follow-up, not yet implemented.

## What it demonstrates

In the derived L5 source graph the public inline function `demo::compute()`
newly carries a `DECL_REFERENCES_DECL` edge to the internal (non-public-header)
declaration `detail::kInternalLimit` that it did not reach in v1 — ADR-041's
*other* headline "not a call at all" example, verbatim:

```cpp
inline int f() { return DETAIL_CONSTANT + 1; }
```

Nothing is called and no type appears in a signature or field — the function
body simply *reads* an internal value. Because the function is inline, its
body is compiled into every caller's translation unit: if `detail::kInternalLimit`
changes value, every already-built consumer keeps the *old* baked-in value
until recompiled, while a freshly recompiled consumer gets the new one — a
silent, version-skew-dependent divergence no artifact diff can see, since the
public declaration `demo::compute()` itself never changed.

Fourth and last of the `DEPENDENCY_EDGE_KINDS` family alongside
[case187](../case187_public_struct_private_field_type/README.md) (`TYPE_HAS_FIELD_TYPE`),
[case188](../case188_public_class_private_base_class/README.md) (`TYPE_INHERITS`),
and [case189](../case189_public_function_private_parameter_type/README.md)
(`DECL_HAS_TYPE`) — this one is the non-call *reference* kind, and the only
one of the four that can only ever be seen by walking an inline/template/constexpr
function's body (`type_graph.py`'s Clang-AST pass, ADR-041 P0 slice 1).

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | an inline function has no exported symbol of its own to diff at all |
| Header AST (L2) | `demo::compute()`'s declaration is unchanged; the header AST does not parse function bodies, so the internal reference is invisible |
| **Source graph (L5)** | the derived reference-dependency delta (`DECL_REFERENCES_DECL`) → the finding |

Per ADR-028 D3 this build/source-evidence finding never decides a shipped-ABI
break on its own; it flags the elevated risk and localizes the cause for
review.

## How to fix

Either promote `detail::kInternalLimit` to a documented public constant, or
keep `demo::compute()`'s inline body independent of internals whose evolution
consumers cannot track — e.g. move the logic out-of-line behind a stable
exported symbol so a value change is at least a proper ABI event instead of a
silent per-TU baked-in constant.
