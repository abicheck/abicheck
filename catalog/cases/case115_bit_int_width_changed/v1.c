#include "v1.h"

void acc_add(Accumulator *a, _BitInt(64) delta) { a->acc += delta; }
_BitInt(64) acc_value(const Accumulator *a) { return a->acc; }
