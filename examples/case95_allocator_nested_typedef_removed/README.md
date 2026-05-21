# Case 95: Allocator Nested-Typedef Removed

**Category:** Source API contract | **Verdict:** 🔴 BREAKING

## What breaks

An allocator-style class drops its historical nested typedef set
(`value_type`, `pointer`, `reference`, `size_type`, `difference_type`).
The exported member functions keep their mangled names, so the .so symbol
table is unchanged and previously-linked binaries still load. Every
consumer source TU that wrote `typename Alloc::value_type` (or
participates in STL-style generic code that does) fails to compile against
v2 headers.

## Why this fires "noise cannon"-style

DWARF stores nested typedefs by their bare name (`value_type`), not
qualified by the containing class. A diff therefore emits
`TYPEDEF_REMOVED` once per removed alias name across the entire library.
For a real allocator + container stack, removing the same five aliases
from `allocator<T>`, `vector<T>::allocator_type`, and friends shows up as
many findings — even though it's a single coherent design change.

## How abicheck catches it

The existing `TYPEDEF_REMOVED` detector fires for each removed alias.
That detection is correct on its own; the noise problem is **policy**.

The new `member_name` suppression selector (added in this batch) lets
maintainers control that noise with a single rule:

```yaml
version: 1
suppressions:
  - member_name: "(value_type|pointer|reference|size_type|difference_type)"
    change_kind: typedef_removed
    reason: "Modernization — STL nested-typedef set removed."
    expires: 2026-12-01
```

`member_name` fullmatches the last `::`-segment of `change.symbol`.
Combined with `change_kind: typedef_removed`, it scopes the rule tightly:
non-typedef changes are not affected, and only the listed aliases are
suppressed.

Combine with `type_pattern` for even tighter scoping when the host class
contributes the qualifier (e.g. when typedef symbols are emitted as
`Allocator::value_type` rather than the bare alias).

## Code diff

| v1 | v2 |
|----|------|
| `typedef int value_type;` and four more nested aliases | (removed) |
| `int* allocate(size_type n);` | `int* allocate(unsigned long n);` |

## How to fix (as a library maintainer)

- If consumers depend on STL-style allocator traits, keep the typedef
  set even when modernizing the public API.
- If you really want them gone, ship a deprecation cycle: in release
  N–1, alias the old names to the new types with `[[deprecated]]`; in
  release N, remove them.

## Real failure demo

```bash
# v1 header, v1 .so:
g++ -std=c++17 -I. app.cpp -L. -lmylib -o app   # compiles and runs

# v2 header, v2 .so: app.cpp's `my_allocator::value_type` is undefined.
g++ -std=c++17 -I. app.cpp -L. -lmylib -o app
# → error: 'value_type' is not a member of 'mylib::my_allocator'
```

## References

- [C++ allocator traits — historical nested types](https://en.cppreference.com/w/cpp/memory/allocator)
- See `tests/test_suppression.py::test_member_name_*` for the selector's
  test coverage.
