#include "old/lib.h"
#include <stdio.h>

int main(void) {
    printf("hook_point(5) = %d\n", hook_point(5));
    printf("compute(5)    = %d\n", compute(5));
    return 0;
}
