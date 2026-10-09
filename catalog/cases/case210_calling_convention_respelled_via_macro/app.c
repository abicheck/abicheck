/* Witness: an app compiled against v1 (literal __attribute__((ms_abi)))
   keeps working against v2, where the same convention is spelled through
   the MATHLIB_CALL macro. Both libraries pass arguments in rcx/rdx/r8/xmm2,
   so the results are correct with either .so: exit 0 both times. */
#include "v1.h"
#include <stdio.h>
#include <math.h>

int main(void) {
    double a[] = {1.0, 2.0, 3.0};
    double b[] = {4.0, 5.0, 6.0};
    double out[3];

    double dot = vector_dot(a, b, 3);
    printf("dot product = %.1f (expected 32.0)\n", dot);

    vector_scale(out, a, 2.0, 3);
    printf("scaled = {%.1f, %.1f, %.1f} (expected {2.0, 4.0, 6.0})\n",
           out[0], out[1], out[2]);

    if (fabs(dot - 32.0) > 0.001 || fabs(out[2] - 6.0) > 0.001) {
        printf("WRONG RESULT: calling convention mismatch\n");
        return 1;
    }
    return 0;
}
