// case108 v1 — mirrors classic TBB's `tbb::task` low-level base class.
//
// In TBB <= 2020, user code subclassed `tbb::task` and overrode `execute()`
// as a virtual. The whole low-level `task` API was removed in oneTBB 2021.1
// (users were redirected to `task_group` / `parallel_invoke`). Removal of a
// publicly-derivable polymorphic base class is the most aggressive shape of
// "exported class removed" — every user subclass becomes a vtable error.
#pragma once

namespace mylib {

class task {
public:
    task();
    virtual ~task();
    virtual task* execute() = 0;
    void set_ref_count(int n);
    int decrement_ref_count();
protected:
    int ref_count_;
};

// Factory that constructs a derived `task` in v1.
task* mylib_spawn_dummy();

} // namespace mylib
