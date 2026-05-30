// case114 v2 — `lib::sort` is now a customisation point object: a
// constexpr variable of an unspecified function-object class type.
//
// Call syntax `lib::sort(a, b)` still works. But `decltype(lib::sort)`
// is now `const lib::__sort_fn`, breaking any extern template, trait
// specialization, or pointer-to-function variable that referred to the
// old function type.
#pragma once

namespace lib {

struct __sort_fn {
    void operator()(int* first, int* last) const;
};

inline constexpr __sort_fn sort{};

} // namespace lib
