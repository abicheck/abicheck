#include "v1.h"
#include <stdio.h>

int main(void) {
    /* v1 ABI: get_count() returns int 42 */
    int n = get_count();
    printf("get_count() = %d\n", n);
    if (n != 42) {
        printf("WRONG RESULT: return type changed/truncated\n");
        return 1;
    }
    return 0;
}
