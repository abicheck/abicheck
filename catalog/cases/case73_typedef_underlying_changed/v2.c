#include "v2.h"
#include <string.h>
#include <stdlib.h>

handle_t handle_open(const char *name) {
    (void)name;
    /* Return an opaque pointer as handle */
    return malloc(1);
}

int handle_read(handle_t h, char *buf, int len) {
    (void)h;
    if (len > 0) {
        memset(buf, 'A', (unsigned)len);
    }
    return len;
}

void handle_close(handle_t h) {
    free(h);
}
