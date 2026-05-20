// case89 v1 — public inline getter touches a pimpl member by name.
//
// Pattern (oneDAL-style):
//
//   class descriptor {
//   public:
//       inline int get_class_count() const { return impl_->class_count_; }
//   private:
//       detail::pimpl<detail::descriptor_impl> impl_;
//   };
//
// The inline body is emitted in every translation unit that includes the
// header — i.e. baked into every CONSUMER binary.
#pragma once
#include <memory>

namespace mylib {
namespace detail {

class descriptor_impl {
public:
    int class_count_ = 2;
    int max_iter_    = 100;
};

template <typename T> using pimpl = std::shared_ptr<T>;

}  // namespace detail

class descriptor {
public:
    descriptor();
    inline int get_class_count() const { return impl_->class_count_; }
    inline int get_max_iter()    const { return impl_->max_iter_; }

private:
    detail::pimpl<detail::descriptor_impl> impl_;
};

}  // namespace mylib
