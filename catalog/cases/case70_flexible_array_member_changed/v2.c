#include "v2.h"
#include <stdlib.h>

struct Packet *packet_create(unsigned int id, unsigned int count) {
    struct Packet *p = malloc(sizeof(*p) + count * sizeof(double));
    if (!p) return NULL;
    p->id = id;
    p->count = count;
    for (unsigned int i = 0; i < count; i++)
        p->data[i] = (double)(i + 1);
    return p;
}

void packet_free(struct Packet *p) { free(p); }

double packet_sum(const struct Packet *p) {
    double sum = 0.0;
    for (unsigned int i = 0; i < p->count; i++)
        sum += p->data[i];
    return sum;
}
