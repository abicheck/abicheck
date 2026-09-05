#include "v1.h"

namespace mylib {

task_arena::task_arena(int concurrency) : concurrency_(concurrency) {}
task_arena::operator int() const { return concurrency_; }

extern "C" int mylib_arena_concurrency(int n) {
    task_arena t(n);
    int as_int = t;  // implicit conversion via `operator int()` (v1)
    return as_int;
}

} // namespace mylib
