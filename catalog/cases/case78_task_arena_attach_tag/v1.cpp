#include "v1.h"

namespace mylib {

task_arena::task_arena() : concurrency_(1) {}

task_arena::task_arena(attach_mode_t mode)
    : concurrency_(mode == attach_to_current ? 4 : 1) {}

int task_arena::concurrency() const { return concurrency_; }

} // namespace mylib
