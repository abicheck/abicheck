// case106 v2 — the conversion operator gains `explicit`.
//
// Source code that wrote `int n = ta;` or relied on implicit conversion
// at a function-call argument boundary no longer compiles. Mangled name
// of `operator int() const` is unchanged, so previously-compiled binaries
// still link.
#pragma once

namespace mylib {

class task_arena {
public:
    task_arena(int concurrency);
    explicit operator int() const;  // NOW EXPLICIT — source break
private:
    int concurrency_;
};

extern "C" int mylib_arena_concurrency(int n);

} // namespace mylib
