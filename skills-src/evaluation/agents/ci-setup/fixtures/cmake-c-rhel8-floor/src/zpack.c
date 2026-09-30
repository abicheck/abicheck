#include <string.h>
#include "zpack/zpack.h"
size_t zpack_bound(size_t n) { return n + 16; }
int zpack_compress(const void *in, size_t n, void *out, size_t *out_n) { memcpy(out, in, n); *out_n = n; return 0; }
