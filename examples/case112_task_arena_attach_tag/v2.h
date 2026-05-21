// case112 v2 — enum replaced by an empty tag struct, attach selected by type.
//
// The oneTBB-2021 idiom is `task_arena::attach{}` — a tag type, not an
// enum value. Both the enum and the int-form constructor disappear; a
// new constructor takes the tag type.
//
//     task_arena ta(task_arena::attach{});  // v2 syntax
//
// Consumer source that wrote `task_arena ta(attach_to_current);` no
// longer compiles. The .so symbol for the old constructor is gone
// (different mangled name), so previously-compiled binaries do not link.
#pragma once

namespace mylib {

class task_arena {
public:
    // Tag type — selects the attach-mode constructor via overload resolution.
    struct attach {};

    task_arena();
    explicit task_arena(attach tag);

    int concurrency() const;

private:
    int concurrency_;
};

} // namespace mylib
