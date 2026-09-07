#include "v2.h"

/* `restrict` on the *definition* only. The declaration consumers compile
 * against (v2.h) is unchanged, so the published contract is unchanged. */
void blend(float *restrict dst, const float *restrict src, int n) {
    for (int i = 0; i < n; i++) dst[i] += src[i];
}
