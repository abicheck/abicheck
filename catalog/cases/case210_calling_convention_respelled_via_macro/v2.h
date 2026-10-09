#ifndef MATHLIB_H
#define MATHLIB_H

#ifdef __cplusplus
extern "C" {
#endif

/* The same Microsoft x64 convention, now spelled through a macro. The
   declarations are token-for-token different from v1 but the convention,
   and therefore the binary interface, is identical. */
#define MATHLIB_CALL __attribute__((ms_abi))

MATHLIB_CALL double vector_dot(const double *a, const double *b, int len);
MATHLIB_CALL void   vector_scale(double *out, const double *in, double factor, int len);

#ifdef __cplusplus
}
#endif

#endif
