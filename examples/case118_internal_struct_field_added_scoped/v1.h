#ifndef CASE118_H
#define CASE118_H

/* ---- Public API (declared in the public header, exported) ---- */
typedef struct { int x; int y; } Point;
Point translate(Point p, int dx, int dy);

/* ---- Internal bookkeeping type ----
 * Declared in the header for other translation units inside the library,
 * but NOT reachable from any public API function. It is therefore not part
 * of the public ABI surface (ADR-024). Changing it is safe for downstream
 * consumers who only use translate()/Point.
 */
struct InternalStats {
    int calls;
};

#endif
