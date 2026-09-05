#include "v2.h"

namespace mylib {

task_arena::task_arena() : concurrency_(1) {}

task_arena::task_arena(attach /*tag*/) : concurrency_(4) {}

int task_arena::concurrency() const { return concurrency_; }

} // namespace mylib
