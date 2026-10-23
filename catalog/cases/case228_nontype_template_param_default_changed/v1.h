#pragma once
// case228 v1 - non-type parameter N defaults to 4
namespace mylib {
template <typename T, int N = 4>
struct Buf {
    T t[N];
};
}  // namespace mylib
