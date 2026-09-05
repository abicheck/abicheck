#include "v1.h"
#include <stdio.h>

int main(void) {
    int val = 42;

    /* Pass int* to process() — v1 expects int*, v2 expects int** */
    process(&val);
    printf("process(&val) succeeded, val = %d\n", val);

    /* get_buffer() returns int* in v1, int** in v2 */
    int *buf = get_buffer();
    buf[0] = 99;
    printf("get_buffer()[0] = %d\n", buf[0]);

    return 0;
}
