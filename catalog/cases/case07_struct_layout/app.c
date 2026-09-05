/* DEMO: intentional ABI mismatch — v2 init_point() writes a 'z' field past
   the v1 stack allocation. The canary detects the corruption. Educational only. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    /* v1 Point has only x,y — 8 bytes on stack */
    Point p = {0, 0};
    unsigned int canary = 0xDEADBEEFu;
    printf("before: p={%d,%d} canary=0x%X\n", p.x, p.y, canary);
    init_point(&p);
    printf("after:  p={%d,%d} canary=0x%X\n", p.x, p.y, canary);
    if (canary != 0xDEADBEEFu)
        printf("CORRUPTION detected! (v2 wrote past end of struct)\n");
    return 0;
}
