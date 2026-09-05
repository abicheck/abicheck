/* bad.c — v1: functions are outlined, symbols exported in .dynsym. */
#include "bad.h"

int fast_abs(int x) { return x < 0 ? -x : x; }
int fast_max(int a, int b) { return a > b ? a : b; }
