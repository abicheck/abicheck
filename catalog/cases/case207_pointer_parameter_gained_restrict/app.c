/* DEMO: the consumer passes two overlapping views of one buffer -- legal
   under v1's contract, undefined under v2's. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    float buf[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    /* dst and src overlap: allowed by v1's signature, forbidden by v2's. */
    blend(buf + 1, buf, 4);
    for (int i = 0; i < 8; i++) printf("%.1f ", buf[i]);
    printf("\n");
    return 0;
}
