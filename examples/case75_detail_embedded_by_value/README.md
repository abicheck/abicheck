# Case 75: Internal `detail::` impl struct embedded by value

**Category:** Internal-leak | **Verdict:** BREAKING

## What breaks

```cpp
namespace mylib::detail { struct table_impl { /* ... */ }; }
class table { detail::table_impl impl_; };   // embedded by value
```

The public `mylib::table` class embeds `mylib::detail::table_impl`
*by value* — no pointer indirection, no pimpl. v2 adds a new
`layout_kind` field to `detail::table_impl`. Because the impl is
embedded by value, every byte of the impl's layout propagates into
the public class:

- `sizeof(mylib::table)` grows by `sizeof(unsigned long)` plus any
  alignment padding.
- Stack-allocated `table` instances in caller code overflow their
  v1-sized slot.
- Containers of `table` (`std::vector<table>`, arrays, etc.) compiled
  against v1 headers compute the wrong stride for v2 binaries.

The author touched only the "internal" struct — but the binary
interface of the *public* class moved with it.

## Real Failure Demo

**Severity: BREAKING / LATENT LAYOUT CORRUPTION**

This minimal app does not trip the corrupted field, but the public `table` embeds a changed `detail::table_impl` by value. Any caller that copies, arrays, or inlines deeper accessors is using the old object layout.

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case76_detail_embedded_by_value_app case76_detail_embedded_by_value_v2

tmp=$(mktemp -d)
cp /tmp/abicheck-examples-build/case76_detail_embedded_by_value/app_v1 "$tmp/"
cp /tmp/abicheck-examples-build/case76_detail_embedded_by_value/libv2.so "$tmp/libv1.so"
(cd "$tmp" && LD_LIBRARY_PATH=. ./app_v1)
# rows=3 cols=4 (expect 3 4)
```

## Why abicheck catches it

The existing `struct_field_added` detector flags the new field on
`detail::table_impl`. By itself that finding looks like a non-public
change. The `internal_type_leaks_via_public_api` overlay walks the
reachability graph from `mylib::table` (a public exported type),
finds that one of its fields has type `mylib::detail::table_impl`,
and surfaces a synthetic finding whose description cites the
embedding path:

```text
mylib::table → field:impl_ → mylib::detail::table_impl
```

The overlay also notes that the leak is *embedded-by-value*, meaning
the change propagates the layout — not just the identity — into the
public class.

## Code diff

```cpp
// v1
namespace mylib::detail {
struct table_impl {
    unsigned long row_count;
    unsigned long column_count;
};
}

// v2 — one extra field on the "internal" struct
namespace mylib::detail {
struct table_impl {
    unsigned long row_count;
    unsigned long column_count;
    unsigned long layout_kind;   // NEW — shifts mylib::table's size
};
}
```

## How to fix

Hold the impl by pointer instead of by value (pimpl) so the public
class's size becomes `sizeof(void*)` and is decoupled from the impl
layout:

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

## References

- Herb Sutter, *Exceptional C++* — the canonical pimpl write-up.
- oneTBB / oneDAL public APIs use pimpl for exactly this reason: the
  internal detail struct can grow across releases without ABI impact.
