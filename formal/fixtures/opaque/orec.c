#include <stdlib.h>
#include "orec.h"
#ifdef REC_V2
struct orec { int a; int x; int b; };
#else
struct orec { int a; int b; };
#endif
struct orec *orec_new(void) {
    struct orec *r = calloc(1, sizeof *r);
    r->a = 1;
#ifdef REC_V2
    r->x = 7;
#endif
    r->b = 42;
    return r;
}
int orec_get_b(const struct orec *r) { return r->b; }
