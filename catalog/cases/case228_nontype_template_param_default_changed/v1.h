#pragma once
// case228 v1 - non-type parameter N defaults to 4
namespace mylib {
template <typename T, int N = 4>
struct Buf {
    T t[N];
};
}  // namespace mylib

// Ordinary export, identical in v1 and v2: gives the library a non-empty
// export table (a PE DLL with none is refused as not a DLL). Same anchor as case122.
int library_version();
