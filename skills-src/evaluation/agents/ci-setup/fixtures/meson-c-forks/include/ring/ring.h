#ifndef RING_H
#define RING_H
#include <stddef.h>
typedef struct ring ring;
ring *ring_new(size_t capacity);
int ring_push(ring *r, int v);
void ring_free(ring *r);
#endif
