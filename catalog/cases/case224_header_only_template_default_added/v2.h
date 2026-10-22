#pragma once
// case224 v2 - default U = int added
namespace mylib {
template <typename T, typename U = int>
struct Box {
    T t;
    U u;
};
}  // namespace mylib
