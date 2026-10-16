/* Witness: built against v1 (sysv_abi). Against v1: 32.0, exit 0. Against v2 (ms_abi) the arguments arrive in other registers: wrong result, exit 1. */
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
