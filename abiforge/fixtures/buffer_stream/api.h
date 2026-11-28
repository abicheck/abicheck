// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stddef.h>

struct buffer {
    unsigned char *data;
    size_t capacity;
    int owned;
};
typedef unsigned char *(*allocate_fn)(size_t);
int reserve(struct buffer *, size_t, allocate_fn);
typedef size_t (*read_fn)(void *, unsigned char *, size_t, int *);
int collect(read_fn, void *, unsigned char *, size_t, size_t *);
