#include "v1.h"
#include <stdio.h>

int main(void) {
    int **m = get_matrix();

    /* App compiled against v1 assumes int element stride. */
    m[0][0] = 5;
    m[0][1] = 7;

    int s = sum_row(m, 0, 2);
    printf("sum_row = %d\n", s);
    printf("expected = 12\n");

    if (s != 12) {
        printf("CORRUPTION: pointer-chain pointee type changed (int** vs long**)\n");
        return 1;
    }
    return 0;
}
