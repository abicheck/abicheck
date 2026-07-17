# case191_header_only_graph_field_type — Same finding, proven with no build integration at all

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `public_api_internal_dependency_added` · **Evidence tier:** L5

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

This case demonstrates the same public-struct-gains-a-private-field-type
scenario as [case187](../case187_public_struct_private_field_type/README.md)
(`demo::Config` gains a `TYPE_HAS_FIELD_TYPE` edge to the internal
`detail::RawConfig`) — but proven through a genuinely different mechanism:
the **header-only-graph addendum** (`header_graph.py`, ADR-041 addendum), which
builds the L5 graph straight from a header dump with **no build integration
required at all** — no `compile_commands.json`, no `--sources` checkout, just
`service.run_dump(..., header_graph=True)` over public headers.

The fixture's old-side graph has **zero** `TYPE_HAS_FIELD_TYPE` edges (unlike
case187, which uses a same-kind self-loop edge as a coverage trick) — instead
it stamps `extractor_passes={"header_type_graph": True}`, the header-only
pass's own confirmed-pass marker. `type_graph._pass_trusted_kinds` grants a
header-only confirmation credit for exactly the three *structural* kinds it
has true project-wide visibility of (`TYPE_INHERITS`/`TYPE_HAS_FIELD_TYPE`/
`DECL_HAS_TYPE` — never the two body-dependent kinds, which a header-only scan
cannot see for out-of-line bodies), so the old side's genuine *absence* of the
edge is trusted as a real, verified zero without needing any edge-presence
trick. This is the coverage-honesty mechanism unique to the header-only-graph
path, distinct from the build-integrated `type_graph`/`call_graph` passes'
own `extractor_passes["type_graph"]`/`["call_graph"]` markers.

## Why this matters

`header_graph.py` exists precisely so a consumer who only has public headers
(no build system access at all — e.g. a downstream packager auditing a
prebuilt SDK) still gets the non-call type-dependency check, at the cost of
weaker resolution than a full per-TU Clang replay would give (bare
unqualified type names when falling back to the flat `AbiSnapshot` model; full
qualified-name resolution when a `clang -ast-dump=json` header AST is
available). It is currently reachable only via the Python API
(`service.run_dump(header_graph=True)`), not the `dump`/`scan` CLI.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | no exported symbol changed |
| Header AST (L2) | `demo::Config`'s own declaration is unchanged from the outside |
| **Header-only graph (L5, no build)** | the derived type-dependency delta (`TYPE_HAS_FIELD_TYPE`) → the finding, with no build system involved |

## How to fix

Either promote `detail::RawConfig` to a documented part of the API, or keep
`demo::Config`'s field types independent of internals whose evolution
consumers cannot track.
