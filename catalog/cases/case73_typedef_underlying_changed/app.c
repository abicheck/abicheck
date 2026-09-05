#include "v1.h"
#include <stdio.h>

int main(void) {
    /* Compiled against v1: handle_t is int (4 bytes on x86-64) */
    handle_t h = handle_open("test.dat");
    printf("handle = %d\n", h);

    char buf[16];
    int n = handle_read(h, buf, 4);
    printf("read %d bytes\n", n);

    handle_close(h);
    printf("Done.\n");
    return 0;
}
