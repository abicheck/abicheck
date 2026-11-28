// SPDX-License-Identifier: Apache-2.0
#include "api.h"
int dispatch(const struct ops *ops, int *calls) {
#ifndef IGNORE_MINIMUM
    if (ops->extent < sizeof(*ops)) return -1;
#endif
    ops->visit(calls);
    return 0;
}

void clear(struct result *result, int *releases) {
#ifndef RELEASE_BORROWED
    if (!result->owned) return;
#endif
    if (result->release) result->release(releases);
    result->owned = 0;
    result->release = NULL;
}
