#pragma once
/* v2: the accumulator widened to _BitInt(128). The storage size and the
   calling-convention treatment of the parameter/return change. (128 is the
   maximum _BitInt width clang supports, so the case stays portable.) */

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    _BitInt(128) acc;
} Accumulator;

void acc_add(Accumulator *a, _BitInt(128) delta);
_BitInt(128) acc_value(const Accumulator *a);

#ifdef __cplusplus
}
#endif
