#pragma once
// case223 v1 - header-only class template, default U = int
namespace mylib {
template <typename T, typename U = int>
struct Box {
    T t;
    U u;
};
}  // namespace mylib
