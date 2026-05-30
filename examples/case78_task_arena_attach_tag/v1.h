// case112 v1 — task_arena with an "attach" mode selected via enum value.
//
// Mirrors the pre-2021 oneTBB API where `task_arena` distinguished
// constructor variants via an `attach_mode_t` enum value:
//
//     task_arena(attach_mode_t = no_attach);
//     // or
//     task_arena ta(attach_to_current);
#pragma once

namespace mylib {

// v1: mode is an enum value, passed by value to the constructor.
enum attach_mode_t {
    no_attach          = 0,
    attach_to_current  = 1,
};

class task_arena {
public:
    task_arena();
    explicit task_arena(attach_mode_t mode);

    int concurrency() const;

private:
    int concurrency_;
};

} // namespace mylib
