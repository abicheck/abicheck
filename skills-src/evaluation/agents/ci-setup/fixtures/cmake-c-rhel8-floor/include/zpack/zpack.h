#ifndef ZPACK_H
#define ZPACK_H
#include <stddef.h>
size_t zpack_bound(size_t n);
int zpack_compress(const void *in, size_t n, void *out, size_t *out_n);
#endif
