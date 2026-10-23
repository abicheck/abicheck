#pragma once
// case224 v1 - no default on U
namespace mylib {
template <typename T, typename U>
struct Box {
    T t;
    U u;
};
}  // namespace mylib
