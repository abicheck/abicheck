// case96 v1 — value type with a hidden-friend `operator==`.
//
// `operator==` is declared *inside* the class body as a friend with an
// inline definition. This is the canonical C++ "hidden friend" pattern:
//
//   - The function lives at namespace scope (mylib::operator==).
//   - It is findable *only via ADL* on one of its argument types —
//     consumers cannot qualify it as `mylib::operator==(a, b)` because
//     the declaration is not visible outside the class body.
//   - The compiler emits it as linkonce_odr inline; typically no
//     external symbol appears in the shared library.
//
// Consumers in the wild simply write `a == b` and the lookup works.
// Removing the friend declaration in v2 silently breaks every such
// call site without changing the .so layout, since there is no symbol
// to remove.
#pragma once

namespace mylib {

class point {
public:
    point(int x, int y);
    int x() const;
    int y() const;

    // Hidden friend — `a == b` resolves to this via ADL.
    friend bool operator==(const point& a, const point& b) {
        return a.x_ == b.x_ && a.y_ == b.y_;
    }

private:
    int x_;
    int y_;
};

extern "C" int mylib_eq(int ax, int ay, int bx, int by);

} // namespace mylib
