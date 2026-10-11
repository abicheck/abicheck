/* Witness: built against v1. vector_dot is ms_abi in both libraries, so the result is 32.0 and exit 0 with either. */
#include "v1.h"
#include <stdio.h>
#include <math.h>

int main(void) {
    double a[] = {1.0, 2.0, 3.0};
    double b[] = {4.0, 5.0, 6.0};
    double dot = vector_dot(a, b, 3);
    printf("dot product = %.1f (expected 32.0)\n", dot);
    if (fabs(dot - 32.0) > 0.001) {
        printf("WRONG RESULT: calling convention mismatch\n");
        return 1;
    }
    return 0;
}
