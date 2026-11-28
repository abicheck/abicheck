// SPDX-License-Identifier: Apache-2.0
#include "api.h"
#include <stdio.h>

static void count(int *n) { ++*n; }

int main(void) {
    struct ops table = {offsetof(struct ops, visit), count};
    int calls = 0, borrowed_releases = 0, owned_releases = 0;
    int short_status = dispatch(&table, &calls);
    int short_calls = calls;
    table.extent = sizeof(table);
    int full_status = dispatch(&table, &calls);
    struct result borrowed = {0, count}, owned = {1, count};
    clear(&borrowed, &borrowed_releases);
    clear(&owned, &owned_releases);
    clear(&owned, &owned_releases);
    printf("%d %d %d %d / %d %d\n", short_status, short_calls, full_status,
           calls, borrowed_releases, owned_releases);
    return !(short_status == -1 && short_calls == 0 && full_status == 0 &&
             calls == 1 && borrowed_releases == 0 && owned_releases == 1);
}
