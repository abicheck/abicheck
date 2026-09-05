#include "v1.h"
#include <stdio.h>

int main(void) {
    struct Point p = make_point(10, 20);

    /* v1 fields: .x and .y. With v2, they are renamed .col/.row
     * but offsets/types are identical → binary layout unchanged.
     * This is a SOURCE-LEVEL (API) break only: old code still runs correctly
     * against the new library, but won't compile against v2 headers.
     */
    printf("p.x = %d\n", p.x);
    printf("p.y = %d\n", p.y);

    if (p.x != 10 || p.y != 20) {
        printf("WRONG RESULT: field layout unexpectedly changed\n");
        return 1;
    }

    printf("OK (API_BREAK only: field renamed, binary layout unchanged)\n");
    return 0;
}
