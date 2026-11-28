// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stddef.h>

struct ops {
    size_t extent;
    void (*visit)(int *);
};
struct result {
    int owned;
    void (*release)(int *);
};
int dispatch(const struct ops *ops, int *calls);
void clear(struct result *result, int *releases);
