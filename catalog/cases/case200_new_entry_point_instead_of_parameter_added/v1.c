#include "v1.h"

int chan_open(const char *name) { return name ? 3 : -1; }
int chan_close(int fd)          { return fd >= 0 ? 0 : -1; }
