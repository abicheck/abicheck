// case94 v1 — empty tag type passed by value into a header-inline template.
//
// Mirrors the oneTBB shape:
//
//   namespace tbb {
//       struct auto_partitioner {};         // empty tag; sizeof == 1
//       template <class Body>
//       inline void parallel_for(int n, Body&& body, auto_partitioner p) { ... }
//   }
//
// At v1 the tag has no state, so callers compile it into their inlined call
// shape as a zero-cost by-value parameter.  Any subsequent layout growth in
// the tag silently mismatches the v1-compiled caller's parameter passing.
#pragma once

namespace mylib {

// Empty tag type, used purely for overload selection in a header-only
// algorithm interface.
struct auto_partitioner {};

class runner {
public:
    runner();
    int run(int n, auto_partitioner p);
};

extern "C" runner* mylib_make_runner();
extern "C" void mylib_free_runner(runner*);

} // namespace mylib
