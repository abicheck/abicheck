#pragma once
// case223 v2 - default U changed to int
namespace mylib {
template <typename T, typename U = int>
struct Box {
    T t;
    U u;
};
}  // namespace mylib
