#include "v1.h"
#include <math.h>
#include <stdio.h>

int main(void) {
    /* Compiled against v1: process(int a, int b) -> 7.0 for (3,4) */
    double result = process(3, 4);
    printf("process(3, 4) = %.1f\n", result);
    printf("Expected: 7.0\n");
    if (fabs(result - 7.0) > 0.01) {
        printf("WRONG RESULT: parameter ABI changed\n");
        return 1;
    }
    return 0;
}
