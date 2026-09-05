#include "lib.h"

/* DEFAULT visibility — allows interposition by LD_PRELOAD or other .so */
int hook_point(int x) { return x * 2; }

int compute(int x) { return hook_point(x) + 1; }
