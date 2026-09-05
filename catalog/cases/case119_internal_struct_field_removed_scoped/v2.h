#ifndef CASE119_H
#define CASE119_H

/* ---- Public API: unchanged ---- */
typedef struct { int x; int y; } Point;
Point translate(Point p, int dx, int dy);

/* ---- Internal type: a field was removed (normally an ABI break) ----
 * Not on the public surface, so --scope-public-headers must not report it.
 */
struct InternalStats {
    int calls;
};

#endif
