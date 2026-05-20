// case108 v2 — `task` removed; replacement is a lightweight task_group.
//
// Uses a plain C function pointer rather than std::function so the example
// builds cleanly across toolchains. (libstdc++ 13's stl_bvector.h uses
// __attribute__((__assume__)) which clang-based castxml doesn't accept,
// and #include <functional> drags it in transitively.)
#pragma once

namespace mylib {

// `task` class removed entirely.
// Replacement: a functor-based interface that mirrors oneTBB's
// `task_group::run(F&&)` shape, simplified to a function-pointer callback.
class task_group {
public:
    using task_fn = void (*)();
    task_group();
    void run(task_fn f);
    void wait();
};

} // namespace mylib
