// case89 v2 — detail::descriptor_impl members renamed (e.g. "modernize
// naming"). The public inline accessors are updated in lockstep, so
// rebuilding the LIBRARY succeeds and rebuilding new CONSUMERS succeeds.
//
// The trap: an OLD consumer was already compiled against v1.h. That
// consumer's binary contains the inlined body `return impl_->class_count_`.
// When the consumer runs against the new descriptor_impl (which only has
// `n_classes_`), the inlined access reads from the wrong offset — or, if
// renaming was accompanied by reordering, reads valid memory of a different
// field and returns silently wrong data.
#pragma once
#include <memory>

namespace mylib {
namespace detail {

class descriptor_impl {
public:
    int n_classes_      = 2;    // was class_count_
    int iteration_cap_  = 100;  // was max_iter_
};

template <typename T> using pimpl = std::shared_ptr<T>;

}  // namespace detail

class descriptor {
public:
    descriptor();
    inline int get_class_count() const { return impl_->n_classes_;     }
    inline int get_max_iter()    const { return impl_->iteration_cap_; }

private:
    detail::pimpl<detail::descriptor_impl> impl_;
};

}  // namespace mylib
