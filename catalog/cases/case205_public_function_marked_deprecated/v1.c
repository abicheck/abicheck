#include "v1.h"

int legacy_open(const char *name) { return name ? 1 : -1; }
int modern_open(const char *name) { return name ? 2 : -1; }
