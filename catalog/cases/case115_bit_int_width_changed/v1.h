#pragma once
/* v1: a fixed-point accumulator using a C23 _BitInt(64). */

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    _BitInt(64) acc;
} Accumulator;

void acc_add(Accumulator *a, _BitInt(64) delta);
_BitInt(64) acc_value(const Accumulator *a);

#ifdef __cplusplus
}
#endif
