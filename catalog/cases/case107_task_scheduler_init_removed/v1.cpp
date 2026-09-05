#include "v1.h"

namespace mylib {

task_scheduler_init::task_scheduler_init(int max_threads)
    : max_threads_(max_threads), active_(true) {}
task_scheduler_init::~task_scheduler_init() {}
void task_scheduler_init::initialize(int max_threads) {
    max_threads_ = max_threads;
    active_ = true;
}
void task_scheduler_init::terminate() { active_ = false; }
bool task_scheduler_init::is_active() const { return active_; }

} // namespace mylib
