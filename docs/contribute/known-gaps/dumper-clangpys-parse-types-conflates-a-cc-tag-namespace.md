---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `dumper_clang.py`'s `parse_types()` conflates a C/C++ tag-namespace identity with an ordinary-namespace typedef identity that happens to share the same spelling — pre-existing, not introduced by PR #719's own changes, but investigated and confirmed reachable in this pass (Codex review, investigated, not fixed)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The code moved to extract/headers/clang/records.py:145, where identity = "::".join([*entry.scope, name]) still uses the anon-typedef fallback name. Tag and ordinary namespaces are not distinguished, so `struct Foo; typedef struct{} Foo;` still collides.

For a legal (if unusual) header like
`struct Foo; typedef struct { int x; } Foo;` — an unrelated forward-only
tag `Foo` and a SEPARATE anonymous struct given the ordinary-namespace
name `Foo` via typedef, which C's two-namespace rule keeps genuinely
distinct — `parse_types()`'s `identity = "::".join([*entry.scope, name])`
computation uses the same bare `name` for both (the tag's own `name`,
and the anonymous record's `anon_names`-derived typedef fallback name),
so the two collide into one `identity` key. Reproduced directly against
`_ClangAstParser`: only ONE `RecordType` named `Foo` is emitted (the
typedef-backed definition), and the unrelated opaque tag `struct Foo;`
is silently absent from the snapshot entirely — the exact regression
PR #719's own opaque-handle-type fix was written to prevent, just
reached through a different, adjacent mechanism (a spelling collision
across namespaces rather than declaration-order/redecl-set instability).
**Not fixed here**: closing it correctly needs the tag-namespace vs.
ordinary-namespace distinction threaded through the `identity` key
itself (and every downstream consumer that currently assumes `identity`
uniquely names one type — `_build_record`, the opaque/deprecated/kind
merge maps this same function already builds), which is a real, if
narrow, data-model change to a function this same PR already revised
three times this session (the opaque-stub fix, the kind-canonicalization
fix, and that fix's own regression fix) — each of which independently
needed careful re-verification against `dumper_clang.py`'s exact
2000-line hard cap. A fourth, differently-shaped change to the same
function under continued review pressure is exactly the risk profile
this file's own "known gaps over risky reactive patches" convention
exists to avoid; a correct fix needs its own dedicated pass with fresh
test coverage for the namespace-collision case specifically, not a
same-session extension.
