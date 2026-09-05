#ifndef MATHLIB_H
#define MATHLIB_H

#ifdef __cplusplus
extern "C" {
#endif

/* Changed to Microsoft x64 calling convention.
   On x86-64 Linux, the default is System V ABI (params in rdi, rsi, rdx, rcx,
   r8, r9 + xmm0-xmm7). The ms_abi uses rcx, rdx, r8, r9 + xmm0-xmm3.
   Callers compiled against v1 pass arguments in the wrong registers. */
__attribute__((ms_abi))
double vector_dot(const double *a, const double *b, int len);

__attribute__((ms_abi))
void   vector_scale(double *out, const double *in, double factor, int len);

#ifdef __cplusplus
}
#endif

#endif
