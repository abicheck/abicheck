#include "v2.h"

void blend(float *restrict dst, const float *restrict src, int n) {
    for (int i = 0; i < n; i++) dst[i] += src[i];
}
