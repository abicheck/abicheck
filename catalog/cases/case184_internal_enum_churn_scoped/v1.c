#include "v1.h"

Point translate(Point p, int dx, int dy) {
    InternalMode m = MODE_A;
    Point r;
    r.x = p.x + dx + (int)m;
    r.y = p.y + dy;
    r.x -= (int)m;
    return r;
}
