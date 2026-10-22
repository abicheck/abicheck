#pragma once
// case223 v1 - header-only class template, default U = T
namespace mylib {
template <typename T, typename U = T>
struct Box {
    T t;
    U u;
};
}  // namespace mylib
