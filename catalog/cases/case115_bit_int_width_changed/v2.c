#include "v2.h"

void acc_add(Accumulator *a, _BitInt(128) delta) { a->acc += delta; }
_BitInt(128) acc_value(const Accumulator *a) { return a->acc; }
