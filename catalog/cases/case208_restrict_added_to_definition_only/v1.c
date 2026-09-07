#include "v1.h"

void blend(float *dst, const float *src, int n) {
    for (int i = 0; i < n; i++) dst[i] += src[i];
}
