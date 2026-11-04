#pragma once
// case223 v1 - header-only class template, default U = int
namespace mylib {
template <typename T, typename U = int>
struct Box {
    T t;
    U u;
};
}  // namespace mylib

// Ordinary export, identical in v1 and v2: gives the library a non-empty
// export table (a PE DLL with none is refused as not a DLL). Same anchor as case122.
int library_version();
