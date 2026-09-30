#include <stdlib.h>
#include "ring/ring.h"
struct ring { size_t cap, len; int *buf; };
ring *ring_new(size_t c) { ring *r = malloc(sizeof *r); r->cap = c; r->len = 0; r->buf = calloc(c, sizeof(int)); return r; }
int ring_push(ring *r, int v) { if (r->len == r->cap) return -1; r->buf[r->len++] = v; return 0; }
void ring_free(ring *r) { free(r->buf); free(r); }
