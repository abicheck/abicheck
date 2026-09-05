#ifndef CASE120_H
#define CASE120_H

/* ---- Public API (exported, header-declared) ---- */
typedef struct { int x; int y; } Point;
Point translate(Point p, int dx, int dy);

/* ---- Internal type, not reachable from the public API ---- */
struct InternalStats {
    int calls;
    long total;
};

#endif
