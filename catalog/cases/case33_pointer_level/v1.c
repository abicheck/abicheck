#include "v1.h"
static int buf[16];
void process(int *data) { buf[0] = *data; }
int *get_buffer(void) { return buf; }
