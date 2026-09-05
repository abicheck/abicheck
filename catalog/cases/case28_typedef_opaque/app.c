#include "v1.h"
#include <stdio.h>

int main(void) {
    /* Compiled against v1: dim_t = int, get_dimension(5) should return 5 */
    dim_t d = get_dimension(5);
    printf("get_dimension(5) = %ld\n", (long)d);

    if (d != 5) {
        printf("WRONG RESULT: typedef underlying type changed (int -> long)\n");
        return 1;
    }
    return 0;
}
