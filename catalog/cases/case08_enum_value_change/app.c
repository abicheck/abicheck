#include "v1.h"
#include <stdio.h>

int main(void) {
    /* Compiled with v1: GREEN=1. get_signal() should return GREEN. */
    Color c = get_signal();
    if (c == GREEN) {
        printf("GREEN (correct)\n");
        return 0;
    }

    printf("WRONG RESULT: expected GREEN, got %d\n", (int)c);
    return 1;
}
