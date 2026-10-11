#ifndef MATHLIB_H
#define MATHLIB_H

/* The convention is chosen through a function-like macro; only its
   argument changes between versions (v1: sysv_abi, v2: ms_abi). */
#define MATHLIB_CC(cc) __attribute__((cc))

double MATHLIB_CC(ms_abi) vector_dot(const double *a, const double *b, int len);

#endif
