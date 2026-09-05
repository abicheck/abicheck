// case94 v2 — the previously-empty tag gains a member.
//
// The header author thinks of `auto_partitioner` as a private implementation
// detail (it has no documented members), so adding a state field looks safe.
// But sizeof(auto_partitioner) just grew from 1 to 8, and every consumer
// compiled against v1 that passes the tag by value into runner::run() is
// now writing an undersized argument into a wider parameter slot — silent
// stack/argument corruption.
#pragma once

namespace mylib {

struct auto_partitioner {
    void* affinity_state_;  // NEW: tag is no longer empty
};

class runner {
public:
    runner();
    int run(int n, auto_partitioner p);
};

extern "C" runner* mylib_make_runner();
extern "C" void mylib_free_runner(runner*);

} // namespace mylib
