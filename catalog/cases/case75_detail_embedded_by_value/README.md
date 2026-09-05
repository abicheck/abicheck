# Case 75: Internal `detail::` Struct Embedded by Value

**Category:** Internal Leak | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

`mylib::table` embeds `mylib::detail::table_impl` **by value** (no pointer
indirection) as its only field. v2 adds a field to the "internal"
`detail::table_impl` struct. Because the embedding is by value, the impl's
extra bytes propagate straight into the public class: `sizeof(mylib::table)`
grows from 16 to 24 bytes. Any caller that stack-allocates, copies, arrays,
or containerizes `table` (`std::vector<table>`, etc.) compiled against v1
computes the wrong size/stride against a v2 binary — the author touched only
a struct named "internal", but the binary layout of the public class moved
with it.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `struct table_impl { unsigned long row_count, column_count; };` | `struct table_impl { unsigned long row_count, column_count, layout_kind; };` |
| `class table { detail::table_impl impl_; };` (16 bytes) | same class, impl_ now 24 bytes → `table` is 24 bytes |

## abicheck command

```bash
g++ -std=c++17 -shared -fPIC -g v1.cpp -o libfoo_v1.so
g++ -std=c++17 -shared -fPIC -g v2.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so -H old=v1.h -H new=v2.h --ast-frontend clang
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- type_size_changed: Size changed: table_impl (128 -> 192 bits)
  > Old code allocates or copies the type with the old size;
    heap/stack corruption, out-of-bounds access.
- type_size_changed: Size changed: table (128 -> 192 bits)
- struct_size_changed: Struct size changed: mylib::table (16 -> 24 bytes)
  > sizeof(T) changed in debug info; confirms layout break visible at
    binary level.

Additions:
- func_added: New public function: layout_kind
- type_field_added_compatible: Field added: table_impl::layout_kind
```

## Minimum evidence

`min_evidence: L2` — the header AST is what lets abicheck see that
`mylib::table::impl_` is declared `detail::table_impl` *by value* (not a
pointer), so the internal struct's size change is known to propagate into
the public class's own size rather than staying an implementation detail.
castxml is the documented default backend for this evidence layer; clang
(`--ast-frontend clang`) is a supported alternative AST frontend used above.

## Why abicheck catches it

The header AST records `table_impl` as an embedded-by-value field of
`table`, and DWARF/header type comparison sees both structs' byte sizes grow
between v1 and v2. Because `table_impl` is not held behind a pointer,
abicheck's layout diff reports the size change on both the internal struct
*and* the public class that embeds it (`struct_size_changed` on
`mylib::table` itself) — the public-facing size change is what makes this
BREAKING rather than an invisible internal edit.

## Runtime failure demonstration

**Severity: BREAKING (latent layout corruption)**

**Scenario:** compile app against v1, swap in v2 `.so` without recompile.
This minimal app only reads the two original fields, so it does not trip a
visible corruption — but it demonstrates that the swap is silent, which is
the real danger: any caller that copies, arrays, or accesses more of
`table` would not be so lucky.

```bash
# Build old library + app
g++ -std=c++17 -shared -fPIC -g v1.cpp -o libfoo.so
g++ -std=c++17 -g app.cpp -L. -lfoo -Wl,-rpath,. -o app
./app
# → rows=3 cols=4 (expect 3 4)

# Swap in new library (no recompile)
g++ -std=c++17 -shared -fPIC -g v2.cpp -o libfoo.so
./app
# → rows=3 cols=4 (expect 3 4)   -- looks fine here, but table is now
#   24 bytes while every v1-compiled caller still allocates 16
```

**Why this is still BREAKING:** the app happens not to read the field that
moved, and its own stack slot for `table` isn't reused by anything that
would visibly corrupt in this minimal repro — but `sizeof(table)` genuinely
changed underneath every v1-compiled consumer. A caller with an array of
`table`, a `std::vector<table>`, or code compiled with a struct field after
a `table` member would silently corrupt adjacent memory.

## Safe redesign

Hold the impl by pointer instead of by value (pimpl) so the public class's
size becomes `sizeof(void*)` and is fully decoupled from the impl's layout:

```cpp
class table {
public:
    table();
    ~table();
    unsigned long row_count() const;
private:
    struct impl;          // forward declaration only
    impl* p_;             // fixed size, no layout leakage
};
```

**Real-world example:** oneDAL / oneTBB public APIs use pointer-pimpl for
exactly this reason — the internal detail struct can grow across releases
without moving the public type's ABI.

## References

- Herb Sutter, *Exceptional C++* — the canonical pimpl write-up.
