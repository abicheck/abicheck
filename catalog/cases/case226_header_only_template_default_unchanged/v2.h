#pragma once
// case226 v2 - same default U = int, comment reworded
namespace mylib {
template <typename T, typename U = int>
struct Box {
    T t;
    U u;
};
}  // namespace mylib
