#include <stdlib.h>
#include "rec.h"
/* The library allocates and fills the object, so an old consumer never
   under-allocates: the only observable difference is which value a load at
   the old offset sees (no out-of-bounds access). */
struct rec *rec_new(void) {
    struct rec *r = calloc(1, sizeof *r);
    r->a = 1;
#ifdef REC_V2
    r->x = 7;
#endif
    r->b = 42;
    return r;
}
int rec_get_b(const struct rec *r) { return r->b; }
