#ifndef MATHLIB_H
#define MATHLIB_H

#ifdef __cplusplus
extern "C" {
#endif

/* Default calling convention (System V AMD64 ABI on Linux: params in rdi, rsi, rdx...) */
double vector_dot(const double *a, const double *b, int len);
void   vector_scale(double *out, const double *in, double factor, int len);

#ifdef __cplusplus
}
#endif

#endif
