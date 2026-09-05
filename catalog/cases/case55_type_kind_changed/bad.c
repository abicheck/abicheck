/* bad.c — v1: Data is a struct with both x and y. */
#include "bad.h"

void data_init(Data *d, int x, int y) {
    d->x = x;
    d->y = y;
}

int data_sum(const Data *d) {
    return d->x + d->y;
}
