#include "v1.h"
#include <stdlib.h>

struct Packet *packet_create(unsigned int id, unsigned int count) {
    struct Packet *p = malloc(sizeof(*p) + count * sizeof(float));
    if (!p) return NULL;
    p->id = id;
    p->count = count;
    for (unsigned int i = 0; i < count; i++)
        p->data[i] = (float)(i + 1);
    return p;
}

void packet_free(struct Packet *p) { free(p); }

float packet_sum(const struct Packet *p) {
    float sum = 0.0f;
    for (unsigned int i = 0; i < p->count; i++)
        sum += p->data[i];
    return sum;
}
