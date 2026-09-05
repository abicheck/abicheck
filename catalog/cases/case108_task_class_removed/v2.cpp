#include "v2.h"

namespace mylib {

task_group::task_group() {}
void task_group::run(task_fn f) { if (f) f(); }
void task_group::wait() {}

} // namespace mylib
