#include "old/lib.h"
#include <stdio.h>

int main(void) {
    union Value v;
    v.l = 0;
    fill(&v);
    printf("after fill: v.l = %ld\n", v.l);
    if (v.l != 42) {
        printf("UNEXPECTED: v.l should be 42\n");
        return 1;
    }
    printf("OK: union field added (no size change) is ABI-compatible\n");
    return 0;
}
