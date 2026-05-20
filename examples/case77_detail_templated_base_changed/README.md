# Case 77: Internal `detail::` *templated* base class layout change

**Category:** Internal-leak | **Verdict:** BREAKING

## What breaks

This mirrors the actual oneDAL pattern from `cpp/oneapi/dal/algo/knn/common.hpp`:

```cpp
namespace mylib::detail {
template <typename Task> class descriptor_base { /* ... */ };
}
template <typename Task = task::classification>
class knn_descriptor : public detail::descriptor_base<Task> { /* ... */ };
```

`detail::descriptor_base` is a **class template**. v2 adds an `int max_iter_`
field, which grows *every instantiation* simultaneously:

- `sizeof(knn_descriptor<task::classification>)` grows
- `sizeof(knn_descriptor<task::regression>)` grows
- The offset of `neighbor_count_` in *every* `knn_descriptor<Task>` shifts

## Difference from case74

| | case74 | case77 |
|---|---|---|
| Detail base | non-template class | **class template** |
| Reachability edge | nominal `base` lookup | template-instantiation traversal |
| Affected public types | one (`knn_descriptor`) | every `knn_descriptor<Task>` instantiation |
| Detector code path | direct base name match | template-argument expansion |

case74 verifies the simple inheritance edge. case77 verifies that the leak
reachability walker follows template-instantiation edges into `detail::`.

## Why abicheck catches it

`type_size_changed` / `type_field_added` fire on each instantiation of
`detail::descriptor_base<...>`. The `internal_type_leaks_via_public_api`
overlay (`abicheck/internal_leak.py`) walks reachability from public symbols
and finds the chain
`knn_descriptor<task::classification> → base:detail::descriptor_base<task::classification>`.
The overlay path is what reviewers can't dismiss as "internal-only".

## Code diff

```cpp
// v1
namespace mylib::detail {
template <typename Task>
class descriptor_base {
public:
    int class_count_;
};
}

// v2 — single new field, but multiplied across every instantiation
namespace mylib::detail {
template <typename Task>
class descriptor_base {
public:
    int class_count_;
    int max_iter_;        // NEW
};
}
```

## Real-world reference

`cpp/oneapi/dal/algo/knn/common.hpp` declares:

```cpp
template <typename Float = float,
          typename Method = method::by_default,
          typename Task = task::by_default,
          typename Distance = oneapi::dal::minkowski_distance::descriptor<Float>>
class descriptor : public detail::descriptor_base<Task>;
```

A single field added to oneDAL's `detail::descriptor_base<Task>` would break
the binary layout of every shipped algorithm descriptor.
