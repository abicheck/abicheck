#pragma once
/* BLAS/LAPACK-style LP64 integer interface (e.g. oneMKL): MKL_INT is 32-bit (int). */

typedef int MKL_INT;

#ifdef __cplusplus
extern "C" {
#endif

/* BLAS-like entry points taking integer dimensions / increments. */
MKL_INT cblas_isamax(MKL_INT n, const float *x, MKL_INT incx);
void cblas_sscal(MKL_INT n, float a, float *x, MKL_INT incx);
void cblas_saxpy(MKL_INT n, float a, const float *x, MKL_INT incx,
                 float *y, MKL_INT incy);
MKL_INT mkl_get_max_threads(void);

#ifdef __cplusplus
}
#endif
