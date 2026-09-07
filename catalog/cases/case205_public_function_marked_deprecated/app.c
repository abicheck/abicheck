/* DEMO: the consumer still calls legacy_open() and still links and runs.
   Recompiling against v2 now emits a deprecation warning at this call site. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    printf("legacy_open -> %d\n", legacy_open("demo"));
    printf("modern_open -> %d\n", modern_open("demo"));
    return 0;
}
