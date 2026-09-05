# Case 189: Public Function Parameter Retyped to an Internal Type

**Category:** Symbol API | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

`demo::configure()`'s opaque `void*` parameter is retyped to
`detail::Options*` — a type declared only in an internal, non-public header
(`detail_private.h`, never named as a public header). Changing a
parameter's type changes the function's mangled name in C++: the old
exported symbol (`configure(void*, Meta)`) genuinely disappears and a
different one (`configure(detail::Options*, Meta)`) appears. Any binary
that calls `configure()` and hasn't recompiled will fail to load.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `void configure(void* opaque, Meta info);` | `void configure(detail::Options* opaque, Meta info);` |

## abicheck command

```bash
g++ -std=c++17 -shared -fPIC -g v1.cpp -o libv1.so
g++ -std=c++17 -shared -fPIC -g v2.cpp -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h --ast-frontend clang
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- func_removed: Public function removed: configure
  > Old binaries call a symbol that no longer exists; dynamic linker
    will refuse to load or crash at call site.

Deployment risk (binary-compatible, review needed):
- public_api_internal_dependency_added: Public entry 'configure' now reaches internal
  declaration(s)/type(s) demo::detail::Options it did not before.
  Proof path: configure --[DECL_HAS_TYPE]--> demo::detail::Options

Additions:
- func_added: New public function: configure (new mangled signature)
- type_added: New type: Options
```

## Minimum evidence

`min_evidence: L5` — the break itself is already visible from DWARF alone
(`func_removed` fires from `-g` debug info with no headers at all, since the
mangled symbol `_ZN4demo9configureEPvNS_4MetaE` simply disappears from the
exported-symbol table), so the verdict does not strictly need L5. What L5
buys is the second finding: the source graph is what names *which* internal
type the new signature now depends on (`public_api_internal_dependency_added`,
naming `demo::detail::Options` and the exact `DECL_HAS_TYPE` edge) — that
correlated context requires the L2 header AST plus the L5 source-graph pass
built on top of it, not DWARF alone.

## Why abicheck catches it

A parameter-type change alters the Itanium mangled name, so
`_ZN4demo9configureEPvNS_4MetaE` is simply absent from v2's dynamic symbol
table — a direct symbol-presence diff at L0/L1, reported as `func_removed`.
Layered on top, the header AST resolves `detail::Options` as a type declared
outside the public header set, and the L5 source graph records
`configure --[DECL_HAS_TYPE]--> demo::detail::Options` to report that the
public surface took on an undeclared dependency on internal code —
correlated context on top of the already-detected symbol removal.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** compile app against v1, swap in v2 `.so` without recompile.

```bash
# Build old library + app
g++ -std=c++17 -shared -fPIC -g v1.cpp -o libv1.so
g++ -std=c++17 -g app.cpp -L. -lv1 -Wl,-rpath,. -I. -o app
./app
# → exit 0

# Swap in new library (no recompile)
g++ -std=c++17 -shared -fPIC -g v2.cpp -o libv1.so
./app
# → ./app: symbol lookup error: ./app: undefined symbol: _ZN4demo9configureEPvNS_4MetaE
```

**Why CRITICAL:** the old mangled symbol is gone from v2's dynamic symbol
table; the runtime linker cannot resolve `configure` and the process fails
to start.

## Safe redesign

Either promote `detail::Options` to a documented, stable part of the public
API, or take/return only public types — e.g. a builder pattern, or an opaque
public handle that wraps the internal type.

## References

- [case160_public_api_internal_dep_added](../case160_public_api_internal_dep_added/README.md) — same finding family via a `DECL_CALLS_DECL` edge (a public function calling an internal one), hand-built fixture.
- [case187_public_struct_private_field_type](../case187_public_struct_private_field_type/README.md) — same finding family via `TYPE_HAS_FIELD_TYPE` (a private field type), real compiled example.
- [case188_public_class_private_base_class](../case188_public_class_private_base_class/README.md) — same finding family via `TYPE_INHERITS` (a private base class), real compiled example.
