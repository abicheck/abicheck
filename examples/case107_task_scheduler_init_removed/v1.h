// case107 v1 — mirrors classic TBB's `task_scheduler_init`.
//
// In TBB <= 2020, applications initialized the runtime by constructing a
// `tbb::task_scheduler_init` object. The class was deprecated in 2020 and
// fully removed in oneTBB 2021.1. Removal of this whole class (including
// its public ctors/dtors and `terminate()`/`initialize()` members) is the
// single biggest hard ABI break in TBB's history.
//
// This case captures the canonical "previously-exported non-polymorphic class
// disappears" shape so we have a named regression fixture. (The vtable / RTTI
// angle of removing a polymorphic base class is covered separately by
// case108, which models the `tbb::task` removal.)
#pragma once

namespace mylib {

class task_scheduler_init {
public:
    static constexpr int automatic = -1;
    explicit task_scheduler_init(int max_threads = automatic);
    ~task_scheduler_init();
    void initialize(int max_threads);
    void terminate();
    bool is_active() const;
private:
    int max_threads_;
    bool active_;
};

} // namespace mylib
