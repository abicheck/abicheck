// case96 v2 — the hidden friend `operator==` is removed.
//
// The class layout, members, constructors, and accessors are
// unchanged. The .so symbol set is unchanged (the inline friend never
// had a public symbol). Yet every consumer that wrote `a == b`
// against v1's headers fails to compile against v2's: ADL no longer
// finds a viable `operator==` on `mylib::point`.
//
// This is the prototypical "castxml header-AST capture" detection
// path described in the roadmap. abicheck reads the `befriending`
// attribute on the v1 `Class` element to identify the hidden friend
// and emits HIDDEN_FRIEND_REMOVED when it disappears in v2.
#pragma once

namespace mylib {

class point {
public:
    point(int x, int y);
    int x() const;
    int y() const;
    // operator== removed — source consumers writing `a == b` no longer compile.
private:
    int x_;
    int y_;
};

extern "C" int mylib_eq(int ax, int ay, int bx, int by);

} // namespace mylib
