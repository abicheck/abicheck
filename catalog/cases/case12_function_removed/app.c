#include "v1.h"
#include <stdio.h>

int main(void) {
    /* Both fast_add and other_func are in v1 */
    printf("fast_add(3, 4) = %d\n", fast_add(3, 4));
    printf("other_func(5)  = %d\n", other_func(5));
    return 0;
}
