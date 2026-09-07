/* DEMO: the consumer keeps calling the original one-argument entry point,
   which v2 preserves unchanged alongside the new chan_open_ex(). */
#include "v1.h"
#include <stdio.h>

int main(void) {
    int fd = chan_open("demo");
    printf("chan_open -> %d (expected 3)\n", fd);
    printf("chan_close -> %d\n", chan_close(fd));
    return 0;
}
