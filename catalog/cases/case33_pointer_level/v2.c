#include "v2.h"
static int buf[16];
static int *buf_ptr = buf;
void process(int **data) { buf[0] = **data; }
int **get_buffer(void) { return &buf_ptr; }
