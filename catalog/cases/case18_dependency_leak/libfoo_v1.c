#include "foo_v1.h"
#include <stdio.h>

/* libfoo implementation — reads ThirdPartyHandle.x */
void process(ThirdPartyHandle* h) {
    printf("process: x=%d\n", h->x);
}

int get_value(const ThirdPartyHandle* h) {
    return h->x;
}
