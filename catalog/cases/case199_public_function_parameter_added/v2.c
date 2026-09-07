#include "v2.h"

int chan_open(const char *name, int flags) { return (name && flags >= 0) ? 3 : -1; }
int chan_close(int fd)                     { return fd >= 0 ? 0 : -1; }
