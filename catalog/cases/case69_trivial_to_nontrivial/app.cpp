#include "v1.h"
#include <cstdio>

int main() {
    /* Compiled against v1: Point is trivially copyable, passed in registers */
    Point a{0.0, 0.0};
    Point b{3.0, 4.0};
    double d = distance(a, b);
    std::printf("distance = %.1f\n", d);
    std::printf("Expected: 5.0\n");
    return 0;
}
