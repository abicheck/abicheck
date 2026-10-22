#pragma once
// case226 v1 - default U = int
namespace mylib {
template <typename T, typename U = int>
struct Box {
    T t;
    U u;
};
}  // namespace mylib
