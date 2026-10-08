/* Witness: an old consumer allocates a Point on the heap with v1's size
   (8 bytes) and lets the library initialise it.  The allocation is followed
   by a guard region filled with a known byte pattern, so a library that
   writes past the v1-sized object is caught deterministically, with no
   dependence on stack layout, optimisation level or a sanitizer.
     v1: x=1 y=2, guard intact            -> exit 0
     v2: init_point() also stores z=3 past the v1 object -> guard corrupted,
         exit 1 */
#include "v1.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define GUARD_BYTES 32
#define GUARD_FILL 0xA5

int main(void) {
    unsigned char *mem = malloc(sizeof(Point) + GUARD_BYTES);
    if (!mem) return 2;
    memset(mem, 0, sizeof(Point));
    memset(mem + sizeof(Point), GUARD_FILL, GUARD_BYTES);
    Point *p = (Point *)mem;

    init_point(p);
    printf("sizeof(Point) as compiled = %zu, p={%d,%d}\n", sizeof(Point), p->x, p->y);

    int status = 0;
    for (size_t i = 0; i < GUARD_BYTES; ++i) {
        if (mem[sizeof(Point) + i] != GUARD_FILL) {
            printf("CORRUPTION: library wrote %zu byte(s) past the v1-sized Point\n", i + 1);
            status = 1;
            break;
        }
    }
    if (p->x != 1 || p->y != 2) status = 1;
    free(mem);
    return status;
}
