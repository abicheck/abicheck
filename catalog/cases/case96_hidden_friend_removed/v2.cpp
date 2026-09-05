#include "v2.h"

namespace mylib {

point::point(int x, int y) : x_(x), y_(y) {}
int point::x() const { return x_; }
int point::y() const { return y_; }

extern "C" int mylib_eq(int ax, int ay, int bx, int by) {
    point a(ax, ay);
    point b(bx, by);
    // v2 no longer has the hidden friend — fall back to a manual
    // field-wise comparison so the library still builds.
    return (a.x() == b.x() && a.y() == b.y()) ? 1 : 0;
}

} // namespace mylib
