/* DEMO: the consumer is unaffected -- only the order of two declarations
   in the header (and of their two definitions in the .c file) changed. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    plot_point(7, 1.5);
    plot_reset();
    printf("plotted (index=7, value=1.5)\n");
    return 0;
}
