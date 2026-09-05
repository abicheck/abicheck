#include "v1.h"

namespace mylib {

task::task() : ref_count_(0) {}
task::~task() {}
void task::set_ref_count(int n) { ref_count_ = n; }
int task::decrement_ref_count() { return --ref_count_; }

namespace {
class dummy_task : public task {
public:
    task* execute() override { return nullptr; }
};
} // namespace

task* mylib_spawn_dummy() { return new dummy_task(); }

} // namespace mylib
