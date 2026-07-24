// G32 Phase 0 "ODR-safe" fixture, side B (see ../README.md, Fixture 2).
// Gives `Point` its full definition. Paired with `tu_a.h`'s forward
// declaration, a correct multi-TU merge (Phase C) combines these into one
// complete `Point` rather than reporting a conflict.
struct Point {
    int x;
    int y;
};

void touches_point(Point *p);
