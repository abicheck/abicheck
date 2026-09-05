#ifndef CASE120_H
#define CASE120_H

/* ---- Public API: unchanged ---- */
typedef struct { int x; int y; } Point;
Point translate(Point p, int dx, int dy);

/* ---- Internal type: fields reordered (offset change, normally an ABI break) ----
 * Not on the public surface, so --scope-public-headers must not report it.
 */
struct InternalStats {
    long total;
    int calls;
};

#endif
