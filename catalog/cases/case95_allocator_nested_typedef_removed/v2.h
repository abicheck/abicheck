// case95 v2 — modernization removes the legacy nested typedefs.
//
// The class keeps the same exported symbols (`allocate` / `deallocate`),
// so previously-linked binaries still load and run.  But every consumer
// source TU that wrote `typename my_allocator::value_type` (or any other
// removed alias) now fails to compile.
//
// NOTE: <cstddef> is intentionally avoided here (see v1.h for why).
// The parameter underlying width matches v1's `size_type` so the
// member-function mangled names stay identical and the only diff is
// the nested typedef-set removal.
#pragma once

namespace mylib {

class my_allocator {
public:
    int* allocate(unsigned long n);
    void deallocate(int* p, unsigned long n);
};

} // namespace mylib
