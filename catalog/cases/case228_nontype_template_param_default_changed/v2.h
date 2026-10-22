#pragma once
// case228 v2 - non-type parameter N defaults to 8
namespace mylib {
template <typename T, int N = 8>
struct Buf {
    T t[N];
};
}  // namespace mylib
