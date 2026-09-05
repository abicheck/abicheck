// case80 v1 — pimpl alias is shared_ptr (oneDAL convention:
//   namespace oneapi::dal::detail {
//     template <typename T> using pimpl = std::shared_ptr<T>;
//   }
// ).
#pragma once
#include <memory>

namespace mylib {
namespace detail {

template <typename T>
using pimpl = std::shared_ptr<T>;

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
