#include "v1.h"

int compute(int x) { return x * 2; }

/* Weak binding (STB_WEAK): can be overridden by a strong definition. */
__attribute__((weak)) int helper(int x) { return x + 1; }
