#include "v1.h"

__attribute__((ms_abi)) double vector_dot(const double *a, const double *b, int len) {
    double sum = 0.0;
    for (int i = 0; i < len; i++)
        sum += a[i] * b[i];
    return sum;
}
