// case110 v2 — rehash hint dropped from the public signature.
//
// `insert(int)` is the only overload now. The mangled name changes
// because the parameter list changed, so the .so symbol is renamed and
// every previously-compiled consumer that called the old form fails to
// link.
#pragma once
// (see v1.h: <cstddef> intentionally avoided — castxml + mingw libstdc++ 15
//  rejects bfloat16 literals in <bits/c++config.h>.)

namespace mylib {

class concurrent_unordered_map_int {
public:
    concurrent_unordered_map_int();

    // v2 signature: hint argument removed.
    void insert(int key);

    int  size() const;

private:
    int size_;
};

} // namespace mylib
