// case80 v2 — pimpl alias switched from shared_ptr to unique_ptr.
//
// "It's still a pointer-to-impl, sizeof matches on most platforms, so
//  it's fine, right?"  No: the *type* of descriptor::impl_ changed, so
//
//   1. Every inline accessor that touches impl_ now mangles differently
//      because the member type is part of any template instantiation that
//      references it.
//   2. The destructor signature/behavior changes (no control block, no
//      atomic refcount), so old consumers that constructed a `descriptor`
//      under v1 and pass it across the v1/v2 boundary leak control blocks
//      or double-delete.
//   3. Copy semantics flip from "shared" to "deleted" — any consumer that
//      relied on copy-construction now fails to compile or invokes a
//      different copy path emitted under v1.
#pragma once
#include <memory>

namespace mylib {
namespace detail {

template <typename T>
using pimpl = std::unique_ptr<T>;   // <-- shared_ptr -> unique_ptr

class descriptor_impl;

}  // namespace detail

class descriptor {
public:
    descriptor();
    ~descriptor();
    int get_class_count() const;
    void set_class_count(int v);

private:
    detail::pimpl<detail::descriptor_impl> impl_;
};

}  // namespace mylib
