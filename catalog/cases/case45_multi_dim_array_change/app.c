#include "v1.h"
#include <stdio.h>

int main(void) {
    Matrix m = {0};
    m.rows = ROWS;
    m.cols = COLS;

    /* Write using v1 layout assumptions (float matrix in caller memory). */
    m.data[0][0] = 1.5f;
    m.data[0][1] = 2.5f;

    /* Read through library API. */
    float a = matrix_get(&m, 0, 0);
    float b = matrix_get(&m, 0, 1);
    float sum = a + b;

    printf("a=%.3f b=%.3f sum=%.3f\n", a, b, sum);
    printf("expected: a=1.500 b=2.500 sum=4.000\n");

    if (sum < 3.999f || sum > 4.001f) {
        printf("CORRUPTION: matrix element type/layout changed (float[][] vs double[][])\n");
        return 1;
    }
    return 0;
}
