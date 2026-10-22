#pragma once
// case227 v1 - nested namespace, default U = T
namespace mylib { namespace detail {
template <typename T, typename U = T>
struct Box {
    T t;
    U u;
};
} }  // namespace mylib::detail
