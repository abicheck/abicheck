#ifndef CASE118_H
#define CASE118_H

/* ---- Public API: unchanged ---- */
typedef struct { int x; int y; } Point;
Point translate(Point p, int dx, int dy);

/* ---- Internal type: gained a field (layout change) ----
 * This is an ABI change to InternalStats, but InternalStats is not on the
 * public surface, so with --scope-public-headers it must NOT be reported.
 */
struct InternalStats {
    int calls;
    int errors;   /* new field */
};

#endif
