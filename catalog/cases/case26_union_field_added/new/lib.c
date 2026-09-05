#include "lib.h"

/* v2 writes the newly added double member; old callers allocate smaller union */
void fill(union Value* v) { v->d = 3.1415926535; }
