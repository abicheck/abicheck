/* DEMO: the consumer calls chan_open() with one argument, as v1 declared.
   Under v2 the callee reads a second argument register the caller never set. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    int fd = chan_open("demo");
    printf("chan_open -> %d (expected 3)\n", fd);
    printf("chan_close -> %d\n", chan_close(fd));
    return 0;
}
