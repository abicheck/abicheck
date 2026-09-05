/* Consumer compiled against the LP64 (v1) header: MKL_INT is 32-bit. */
#include "v1.h"

int main(void) {
    float x[4] = {1.0f, -3.0f, 2.0f, 0.5f};
    MKL_INT n = 4;
    MKL_INT idx = cblas_isamax(n, x, 1);
    cblas_sscal(n, 2.0f, x, 1);
    /* Against the ILP64 (v2) library, idx and the threads return are 64-bit:
       the 32-bit caller reads the wrong width. */
    return (int)idx + (int)mkl_get_max_threads();
}
