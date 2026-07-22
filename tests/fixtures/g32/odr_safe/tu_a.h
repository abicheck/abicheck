// G32 Phase 0 "ODR-safe" fixture, side A (see ../README.md, Fixture 2).
// `Point` is only forward-declared here; `tu_b.h` gives the full definition.
// A correct multi-TU merge (Phase C) must not treat this as a conflict —
// the two declarations describe the same type at different completeness.
struct Point;

void touches_point(Point *p);
