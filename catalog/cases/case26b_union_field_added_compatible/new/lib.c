#include "lib.h"

/* v2 still writes via the same long member; adding int i is inert at runtime */
void fill(union Value* v) { v->l = 42; }
