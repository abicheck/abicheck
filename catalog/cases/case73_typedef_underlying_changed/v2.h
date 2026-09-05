#ifndef HANDLE_H
#define HANDLE_H

/* v2: handle_t widened to 64-bit pointer — changes sizeof and ABI.
   On x86-64: sizeof(int) = 4, sizeof(void*) = 8.
   Callers compiled against v1 pass 4 bytes; v2 expects 8. */
typedef void *handle_t;

handle_t handle_open(const char *name);
int handle_read(handle_t h, char *buf, int len);
void handle_close(handle_t h);

#endif
