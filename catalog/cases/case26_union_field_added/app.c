#include "old/lib.h"
#include <stdio.h>

int main(void) {
    union Value v;
    v.i = 0;
    fill(&v);
    printf("after fill: v.i = %d\n", v.i);
    /* IEEE754: double 3.1415926535 has low 32 bits = 0x54442D18 = 1413754136 (never == 42).
     * Safe on any IEEE754-compliant platform (x86-64, ARM64, RISC-V, POWER). */
    if (v.i == 42) {
        printf("unexpected old-compatible value\n");
        return 0;
    }
    printf("UNION_SIZE_MISMATCH: v2 wrote different representation (possible overflow with old layout)\n");
    return 2;
}
