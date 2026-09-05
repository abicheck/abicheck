// case114 — consumer calls lib::sort with the same syntax in v1 and v2.
// The breakage is at decltype / trait-specialization sites; the bare
// call compiles and runs unchanged.
#include "v1.h"

int main() {
    int a[3] = {3, 1, 2};
    lib::sort(a, a + 3);
    return 0;
}
