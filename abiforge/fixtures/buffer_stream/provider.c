// SPDX-License-Identifier: Apache-2.0
#include "api.h"
#include <stdlib.h>

int reserve(struct buffer *buf, size_t wanted, allocate_fn allocate) {
#ifndef ALWAYS_GROW
    if (wanted <= buf->capacity) return 0;
#endif
#ifndef GROW_BORROWED
    if (!buf->owned) return -1;
#endif
    unsigned char *next = allocate(wanted);
    if (!next) return -1;
    for (size_t i = 0; i < buf->capacity && i < wanted; ++i)
        next[i] = buf->data[i];
    if (buf->owned) free(buf->data);
    buf->data = next;
    buf->capacity = wanted;
    buf->owned = 1;
    return 0;
}

int collect(read_fn read_part, void *source, unsigned char *out, size_t cap, size_t *used) {
    *used = 0;
    while (*used < cap) {
        int error = 0;
        size_t left = cap - *used;
        size_t count = read_part(source, out + *used, left, &error);
        if (!count) {
#ifdef HIDE_ERROR
            return 0;
#else
            return error;
#endif
        }
        if (count > left) return -1;
        *used += count;
#ifdef SHORT_IS_EOF
        if (count < left) break;
#endif
    }
    return 0;
}
