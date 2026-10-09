#ifndef MATHLIB_H
#define MATHLIB_H

#ifdef __cplusplus
extern "C" {
#endif

/* Microsoft x64 calling convention, spelled literally. */
__attribute__((ms_abi))
double vector_dot(const double *a, const double *b, int len);

__attribute__((ms_abi))
void   vector_scale(double *out, const double *in, double factor, int len);

#ifdef __cplusplus
}
#endif

#endif
