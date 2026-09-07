/* DEMO: the consumer passes (int, double) as v1 declared. Under v2 the
   callee reads (double, int) -- the integer and the float are taken from
   different register files, so both arguments are garbage. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    plot_point(7, 1.5);
    plot_reset();
    printf("plotted (index=7, value=1.5)\n");
    return 0;
}
