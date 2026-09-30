#include <math.h>
#include "geom/geom.h"
#include "internal.h"
double geom_distance(const geom_point *a, const geom_point *b) { return sqrt(geom__sq(a->x - b->x) + geom__sq(a->y - b->y)); }
int geom_version(void) { return 110; }
