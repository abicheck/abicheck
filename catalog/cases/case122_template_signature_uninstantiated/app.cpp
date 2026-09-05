#ifdef USE_V2
#include "v2.h"
#else
#include "v1.h"
#endif
#include <cstdio>

int main() {
    // Consumer instantiates the template itself. Against v1 this mangles with
    // `int` parameters; against v2 with `long`. The library ships neither
    // instantiation, so the break lives entirely in consumer source.
    printf("%d %d\n", clamp<int>(5, 0, 10), library_version());
    return 0;
}
