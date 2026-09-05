#include "foo_v2.h"
#include <stdio.h>

/* v2: ThirdPartyHandle gained a new field 'y'. This library is compiled
   against foo_v2.h (ThirdPartyHandle = {int x, int y} = 8 bytes).
   If the caller only allocated 4 bytes (v1 layout), writing to h->y
   corrupts the caller's adjacent memory. */
void process(ThirdPartyHandle* h) {
    printf("process: x=%d\n", h->x);
    /* WRITE to y — this is 4 bytes past the v1 allocation boundary */
    h->y = 0xBADC0DE;
    printf("process: wrote y=0x%X at offset 4\n", h->y);
}

int get_value(const ThirdPartyHandle* h) {
    return h->x;
}
