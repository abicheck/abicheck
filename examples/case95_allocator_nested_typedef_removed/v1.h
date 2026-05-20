// case95 v1 — allocator-style class with the full STL "nested typedef" set.
//
// Mirrors the historical std::allocator<T> shape from C++03/11:
// `value_type`, `pointer`, `reference`, `size_type`, `difference_type`.
// Real-world consumers do `typename Alloc::value_type` and the like, so
// removing any of these is a source break even though the .so symbol
// table is untouched.
//
// NOTE: we deliberately avoid <cstddef> and use plain integral types
// for `size_type` / `difference_type`. castxml on Windows ships a clang
// frontend that rejects mingw libstdc++ 15's `<bits/c++config.h>` —
// it uses `__decltype(0.0bf16) __bfloat16_t;` which clang doesn't parse.
// The typedef-set narrative does not depend on the exact underlying
// width; plain `unsigned long` / `long` keep the demo intact.
#pragma once

namespace mylib {

class my_allocator {
public:
    // Historical nested type aliases that consumers depend on.
    typedef int           value_type;
    typedef int*          pointer;
    typedef int&          reference;
    typedef unsigned long size_type;
    typedef long          difference_type;

    int* allocate(size_type n);
    void deallocate(int* p, size_type n);
};

} // namespace mylib
