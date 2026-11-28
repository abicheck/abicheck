// SPDX-License-Identifier: Apache-2.0
#include "api.h"
#include <stdlib.h>
#include <string.h>

static int allocations;
static unsigned char *counted(size_t size) {
    ++allocations;
    return malloc(size);
}

static int capacity(void) {
    unsigned char local[] = {42};
    struct buffer owned = {malloc(1), 1, 1};
    if (!owned.data) return 8;
    owned.data[0] = 42;
    unsigned char *original = owned.data;
    int failed = reserve(&owned, 1, counted) || allocations || owned.data != original;
    allocations = 0;
    failed |= reserve(&owned, 3, counted) || allocations != 1 ||
              owned.capacity < 3 || owned.data[0] != 42;
    free(owned.data);
    struct buffer borrowed = {local, 1, 0};
    allocations = 0;
    failed |= reserve(&borrowed, 1, counted) || allocations || borrowed.data != local;
    int status = reserve(&borrowed, 2, counted);
    int changed = status != -1 || allocations || borrowed.data != local || borrowed.capacity != 1;
    if (borrowed.owned) free(borrowed.data);
    return failed | (changed << 1);
}

struct source { unsigned calls; int error; };
static size_t part(void *context, unsigned char *out, size_t cap, int *error) {
    struct source *source = context;
    const char *chunks[] = {"ab", "c"};
    unsigned i = source->calls++;
    if (i >= 2) {
        *error = source->error;
        return 0;
    }
    size_t n = strlen(chunks[i]);
    if (n > cap) abort();
    memcpy(out, chunks[i], n);
    return n;
}

int main(void) {
    int result = capacity();
    for (int error = 0; error <= 1; ++error) {
        struct source source = {0, error};
        unsigned char data[8] = {0};
        size_t used;
        int status = collect(part, &source, data, sizeof(data), &used);
        if (source.calls != 3 || used != 3 || memcmp(data, "abc", 3)) result |= 4;
        if (status != error) result |= 8;
    }
    return result;
}
