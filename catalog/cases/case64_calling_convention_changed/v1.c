#include "v1.h"

double vector_dot(const double *a, const double *b, int len) {
    double sum = 0.0;
    for (int i = 0; i < len; i++)
        sum += a[i] * b[i];
    return sum;
}

void vector_scale(double *out, const double *in, double factor, int len) {
    for (int i = 0; i < len; i++)
        out[i] = in[i] * factor;
}
