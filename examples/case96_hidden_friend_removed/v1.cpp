#include "v1.h"

namespace mylib {

point::point(int x, int y) : x_(x), y_(y) {}
int point::x() const { return x_; }
int point::y() const { return y_; }

// NOTE: deliberately does NOT use `a == b` inside the library, so the
// inline hidden friend is never ODR-used here and no external symbol
// for `operator==` ends up in libv1.so. The break is therefore
// source-only — the .so files are layout-equivalent across versions.
extern "C" int mylib_eq(int ax, int ay, int bx, int by) {
    point a(ax, ay);
    point b(bx, by);
    return (a.x() == b.x() && a.y() == b.y()) ? 1 : 0;
}

} // namespace mylib
