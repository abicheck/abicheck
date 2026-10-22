#pragma once
// case227 v2 - nested namespace, default U = long
namespace mylib { namespace detail {
template <typename T, typename U = long>
struct Box {
    T t;
    U u;
};
} }  // namespace mylib::detail
