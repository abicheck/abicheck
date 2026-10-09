#ifndef MATHLIB_H
#define MATHLIB_H

#include "v1_config.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The convention comes from MATHLIB_CALL (see v1_config.h). */
double MATHLIB_CALL vector_dot(const double *a, const double *b, int len);
void   MATHLIB_CALL vector_scale(double *out, const double *in, double factor, int len);

#ifdef __cplusplus
}
#endif

#endif
