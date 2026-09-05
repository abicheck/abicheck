#include "v1.h"
Point translate(Point p, int dx, int dy) {
    Point r; r.x = p.x + dx; r.y = p.y + dy; return r;
}
