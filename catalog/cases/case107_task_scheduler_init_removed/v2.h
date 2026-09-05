// case107 v2 — the entire class is removed; users are expected to migrate
// to scoped `global_control` and `task_arena` (oneTBB 2021.1 migration).
#pragma once

namespace mylib {

// task_scheduler_init removed.
// Users must migrate to:
//   - tbb::global_control for thread-count control
//   - tbb::task_arena for explicit arena lifetime
//
// Replacement helper (so this library still has *something* to link):
int active_concurrency();

} // namespace mylib
