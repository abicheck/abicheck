#include "v1.h"

MKL_INT cblas_isamax(MKL_INT n, const float *x, MKL_INT incx) {
    (void)x; (void)incx;
    return n > 0 ? 0 : -1;
}
void cblas_sscal(MKL_INT n, float a, float *x, MKL_INT incx) {
    for (MKL_INT i = 0; i < n; ++i) x[i * incx] *= a;
}
void cblas_saxpy(MKL_INT n, float a, const float *x, MKL_INT incx,
                 float *y, MKL_INT incy) {
    for (MKL_INT i = 0; i < n; ++i) y[i * incy] += a * x[i * incx];
}
MKL_INT mkl_get_max_threads(void) { return 1; }
