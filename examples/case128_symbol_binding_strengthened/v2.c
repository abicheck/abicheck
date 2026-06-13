#include "v2.h"

int compute(int x) { return x * 2; }

/* Strong binding (STB_GLOBAL): can no longer be silently overridden. */
int helper(int x) { return x + 1; }
