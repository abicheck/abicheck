#ifndef MATHLIB_H
#define MATHLIB_H

/* The convention macro is scoped to this header: it is #undef'd right after
   the declarations that use it, so it no longer exists at the end of the
   translation unit. v1 spells sysv_abi (the x86-64 System V default), v2
   ms_abi. */
#define MATHLIB_CALL __attribute__((sysv_abi))

MATHLIB_CALL double vector_dot(const double *a, const double *b, int len);

#undef MATHLIB_CALL

#endif
