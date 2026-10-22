#pragma once
// case223 v2 - default U changed to long
namespace mylib {
template <typename T, typename U = long>
struct Box {
    T t;
    U u;
};
}  // namespace mylib
