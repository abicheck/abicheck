#pragma once
// case227 v1 - nested namespace, default U = int
namespace mylib { namespace detail {
template <typename T, typename U = int>
struct Box {
    T t;
    U u;
};
} }  // namespace mylib::detail
