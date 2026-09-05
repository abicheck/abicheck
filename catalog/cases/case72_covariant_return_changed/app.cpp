#include "v1.h"
#include <cstdio>

/* v1-compiled raw layout assumption: [vptr][radius_] */
struct CircleV1Layout {
    void *vptr;
    int radius;
};

int main() {
    Circle c(5);
    Circle *copy = c.clone();

    int r_virtual = copy->radius();
    int a_virtual = copy->area();
    int r_raw = reinterpret_cast<CircleV1Layout *>(copy)->radius;

    std::printf("clone radius() = %d (expected 5)\n", r_virtual);
    std::printf("clone area()   = %d (expected 75)\n", a_virtual);
    std::printf("raw radius(v1 layout) = %d (expected 5)\n", r_raw);

    delete copy;

    if (r_virtual != 5 || a_virtual != 75 || r_raw != 5) {
        std::printf("WRONG RESULT: covariant return/hierarchy change broke old layout assumptions\n");
        return 1;
    }
    return 0;
}
