# Case 105: Concept Tightening (C++20)

**Category:** Subtle source break / regression suite | **Ground truth:** 🔵
API_BREAK (`concept_tightened`). Object/DWARF/castxml lanes return NO_CHANGE,
but that is an L0–L2 missed detection; L4 source-ABI replay proves the one
canonical verdict.

## What breaks

A C++20 `concept` gains an additional requirement (e.g. `Addable`
previously required only `a + b`; v2 additionally requires `T()`,
default-constructibility). The mangled name of the already-shipped
instantiation (`sum<int>`) is unchanged, so previously-compiled
binaries keep linking. The break is at the *consumer* call site: any
consumer instantiating `sum<T>` against a type that fails the new
requirement no longer compiles against v2's header.

This is the prototypical "concept tightening" case. The change is
invisible at the binary layer and to the default castxml-based header
comparison — but the L4 source-ABI replay path (below) does catch it.

## Why this is in the regression suite

Concept tightening is the C++20 evolution of the older SFINAE-narrowing
pattern (`std::enable_if<...>`): the library author narrows the set of
types a template accepts in a way that the symbol table cannot reveal.
Many algorithm-heavy libraries (for example oneTBB and the standard
library) do this on purpose — sometimes to fix a latent bug, sometimes to nudge
users toward "better" types — and every such tightening is a silent
source-break for whoever was relying on the relaxed contract.

## How abicheck catches it (and where it doesn't)

The default (object/DWARF/castxml) comparison exposes:

- nothing on the `sum<int>` instantiation (mangled name unchanged, the
  exported symbol set is identical between v1 and v2)
- nothing on the concept itself — castxml emits C++20 concept
  declarations as

  ```xml
  <Unimplemented kind="Concept"/>
  ```

  with no name, no body, and no link to the templates that use the
  concept. There is *no way* to detect concept tightening from the
  castxml dump path.

That castxml limitation is permanent, but it is not the whole story: the
L4 source-ABI replay path (`--sources`, ADR-030) uses a clang-based
extractor instead of castxml, which *does* emit named concept
declarations with a constraint hash (`abicheck/buildsource/source_extractors/clang.py`'s
`_emit_concept`). `abicheck/buildsource/source_diff.py`'s `_diff_concepts`
compares that hash and reports a tightened constraint as
`ChangeKind.CONCEPT_TIGHTENED` (API_BREAK). Verified end to end against
this case's real `v1.h`/`v2.h`: with L3 compile-unit evidence + an
explicit public-header root (`abicheck collect -H`) and
`--no-scope-public-headers` on `compare` (see case122's README for why
that flag is needed — the same castxml-can't-see-it gap applies to the
default public-surface scoping), the comparison reports
`concept_tightened` on `Addable` with verdict `API_BREAK`, exactly as
`ground_truth.json` expects. Only the *symmetric* direction — a concept
that loses a requirement (`CONCEPT_RELAXED`) — remains unimplemented.

This case is preserved as a regression fixture demonstrating both the
default-mode gap and the L4 replay that closes it.

## Code diff

| v1 | v2 |
|----|------|
| `concept Addable = requires(T a, T b) { a + b; };` | adds `T()` to the requirement set |
| `sum<wrapped>` compiles (wrapped has `operator+`) | `sum<wrapped>` fails (no default ctor) |

## Real Failure Demo

**Severity: KNOWN_GAP / SOURCE BREAK**

```bash
# v1 header: app.cpp compiles. wrapped satisfies Addable.
g++ -std=c++20 -I. app.cpp -L. -lv1 -o app
./app   # → sum<int>(2, 3) = 5

# v2 header: same app.cpp, the addressing of
# `cs_check_addable_only<wrapped>` no longer satisfies the tightened
# concept and the source fails:
g++ -std=c++20 -I. app.cpp -L. -lv2 -o app
# → error: template constraint failure for ‘template<Addable T>’
# → note: the expression ‘T()’ would be ill-formed
```

## How to fix (as a library maintainer)

- Stage the tightening across a deprecation window: ship the looser
  concept alongside a deprecated alias that warns, then remove.
- Provide a SFINAE-friendly migration: expose a second template that
  preserves the old contract, marked `[[deprecated]]`.
- For internal-only concepts, prefix with `detail::` and document them
  as not-API.

## References

- [P0892R2 `concept` definition syntax](https://wg21.link/P0892R2)
- [cppreference: `requires`-expression](https://en.cppreference.com/w/cpp/language/requires)
- castxml limitation: concepts emitted as `<Unimplemented kind="Concept"/>`
  (no name, no body) — see [castxml issue tracker](https://github.com/CastXML/CastXML/issues?q=concept).
