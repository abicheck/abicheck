# Case 80: Pimpl alias changed from `shared_ptr` to `unique_ptr`

**Category:** Pimpl ABI | **Verdict:** BREAKING

## What breaks

oneDAL's pimpl alias is defined as

```cpp
namespace oneapi::dal::detail {
    template <typename T> using pimpl = std::shared_ptr<T>;
}
```

…and every public class holds its implementation through that alias:

```cpp
detail::pimpl<descriptor_impl> impl_;
```

In v2 the alias is rewritten as `using pimpl = std::unique_ptr<T>`. On most
64-bit platforms `sizeof(shared_ptr<T>) == 16` and `sizeof(unique_ptr<T>) == 8`,
so the containing class even shrinks — the failure isn't "size grew", it is:

1. **Mangled name of every inline accessor changes.** Any header-inlined
   getter that touches `impl_` is templated on the pimpl type; its mangled
   symbol differs between v1 and v2.
2. **Destruction model changes.** No control block, no atomic refcount;
   consumers built against v1 that aliased into the same control block via
   `shared_ptr` aliasing constructors now double-free.
3. **Copy semantics flip.** v1 was copyable (refcounted share). v2 is
   move-only. Consumer code that copied a `descriptor` compiles under v1,
   refuses under v2.

## Why abicheck catches it

The existing `type_field_type_changed` detector fires on `descriptor::impl_`
(old type `std::shared_ptr<detail::descriptor_impl>`, new type
`std::unique_ptr<detail::descriptor_impl>`). The `internal_type_leaks_via_public_api`
overlay (from PR #238) then escalates it because the new field type
references the same `detail::` namespace that the leak detector already
tracks as internal.

## Code diff

```cpp
// v1
template <typename T> using pimpl = std::shared_ptr<T>;

// v2 — silent ownership-model change with cascading consequences
template <typename T> using pimpl = std::unique_ptr<T>;
```

## Why a separate case (not subsumed by case41)

case41 (`type_changes`) covers generic field-type changes. case80 is the
*pimpl-shaped* instance worth carrying as a named regression: the failure
involves three orthogonal axes (mangling, destruction, copyability) and is
specifically the kind of change a maintainer might propose during "modernize
the API" cleanup without realizing it is binary-incompatible.
