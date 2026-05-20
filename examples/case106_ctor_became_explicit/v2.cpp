#include "v2.h"

namespace mylib {

task_arena::task_arena(int concurrency) : concurrency_(concurrency) {}
task_arena::operator int() const { return concurrency_; }

extern "C" int mylib_arena_concurrency(int n) {
    task_arena t(n);
    int as_int = static_cast<int>(t);  // explicit cast required (v2)
    return as_int;
}

} // namespace mylib
