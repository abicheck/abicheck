// case110 v1 — concurrent map with a hash-rehash hint as 2nd parameter.
//
// Mirrors a historical oneTBB API where `insert` carried an extra hint
// argument that interacted with the bucket-rehash policy. Modern oneTBB
// dropped the hint, simplifying the signature.
//
// NOTE: <cstddef> is intentionally avoided. castxml on Windows uses
// clang as a frontend and clang rejects mingw libstdc++ 15's
// `<bits/c++config.h>` (`__decltype(0.0bf16)` bfloat16 literal). We
// use `unsigned long` for the rehash-hint parameter to keep the demo
// portable; the API-drift narrative is unaffected by the exact width.
#pragma once

namespace mylib {

class concurrent_unordered_map_int {
public:
    concurrent_unordered_map_int();

    // v1 signature: 2nd argument is a rehash hint.
    void insert(int key, unsigned long rehash_hint);

    int  size() const;

private:
    int size_;
};

} // namespace mylib
