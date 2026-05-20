// case106 v1 — implicit converting *conversion operator*.
//
// Mirrors the oneTBB-style pattern where a handle type allows implicit
// conversion to a primitive at the call site:
//
//     mylib::task_arena ta(4);
//     int n = ta;          // implicit conversion via `operator int()`
//     accept(ta);          // pass-by-value through implicit conversion
//
// The library author later tightens the API by marking the conversion
// operator `explicit`. The mangled name is unchanged so binaries keep
// linking; every consumer source that relied on implicit conversion now
// fails to compile.
//
// (We use a conversion operator rather than a converting constructor
// because conversion operators carry mangled names through both castxml
// and DWARF paths, while user-declared constructors only get a mangled
// name through DWARF — castxml emits constructors without one.)
#pragma once

namespace mylib {

class task_arena {
public:
    task_arena(int concurrency);
    operator int() const;  // implicit conversion to int — v1
private:
    int concurrency_;
};

extern "C" int mylib_arena_concurrency(int n);

} // namespace mylib
