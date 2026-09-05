#include "v1.h"
#include <string.h>

static int next_handle = 1;

handle_t handle_open(const char *name) {
    (void)name;
    return next_handle++;
}

int handle_read(handle_t h, char *buf, int len) {
    (void)h;
    if (len > 0) {
        memset(buf, 'A', (unsigned)len);
    }
    return len;
}

void handle_close(handle_t h) {
    (void)h;
}
