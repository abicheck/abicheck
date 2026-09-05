#include "old/lib.h"
#include <stdio.h>

int main(void) {
    union Data d;
    init_data(&d);
    printf("d.f = %e (expected ~3.14)\n", d.f);
    if (d.f < 3.0f || d.f > 3.3f) {
        printf("UNION_MISMATCH: removed float field changed interpretation\n");
        return 2;
    }
    return 0;
}
