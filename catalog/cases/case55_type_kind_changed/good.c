/* good.c — v2: Data is now a union (x and y overlap). */
#include "good.h"

void data_init(Data *d, int x, int y) {
    (void)y;
    d->x = x;  /* y overlaps x — only one value stored */
}

int data_sum(const Data *d) {
    return d->x;  /* y == x in a union */
}
