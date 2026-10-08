#include "v2.h"

/* v2 accepts exactly two open modes; anything else is rejected. */
int chan_open(const char *name, int flags) {
    return (name && (flags == CHAN_RDONLY || flags == CHAN_RDWR)) ? 3 : -1;
}
int chan_close(int fd)                     { return fd >= 0 ? 0 : -1; }
