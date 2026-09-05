#ifndef HANDLE_H
#define HANDLE_H

/* v1: handle_t is a 32-bit integer */
typedef int handle_t;

handle_t handle_open(const char *name);
int handle_read(handle_t h, char *buf, int len);
void handle_close(handle_t h);

#endif
