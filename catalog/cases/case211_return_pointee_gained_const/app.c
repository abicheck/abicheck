/* Witness: an old consumer that stores get_name()'s result in a plain
   char *, which v1's declaration permits.
     - Built against v1 and run against v1 or v2: prints "libname", exit 0
       (the binary interface is unchanged -- same register, same pointer).
     - Rebuilt against v2: the initialisation below discards the const
       qualifier -- a constraint violation in C (GCC 14+ / -Werror and every
       C++ compiler reject it). That is the source break. */
#include "v1.h"
#include <stdio.h>
#include <string.h>

int main(void) {
    char *p = get_name();
    printf("name = %s\n", p);
    return strcmp(p, "libname") == 0 ? 0 : 1;
}
