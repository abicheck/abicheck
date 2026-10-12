#ifndef MATHLIB_H
#define MATHLIB_H

/* vector_dot is ms_abi in both versions. After the declaration the header
   redefines MATHLIB_CALL for later use; only that later definition differs
   (v1: sysv_abi, v2: ms_abi), and it cannot reach the declaration above it. */
#define MATHLIB_CALL __attribute__((ms_abi))

MATHLIB_CALL double vector_dot(const double *a, const double *b, int len);

#undef MATHLIB_CALL
#define MATHLIB_CALL __attribute__((ms_abi))

#endif
