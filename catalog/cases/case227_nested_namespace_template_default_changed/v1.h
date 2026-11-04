#pragma once
// case227 v1 - nested namespace, default U = int
namespace mylib { namespace detail {
template <typename T, typename U = int>
struct Box {
    T t;
    U u;
};
} }  // namespace mylib::detail

// Ordinary export, identical in v1 and v2: gives the library a non-empty
// export table (a PE DLL with none is refused as not a DLL). Same anchor as case122.
int library_version();
