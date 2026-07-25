// G32 Phase 0 "ODR conflict" fixture, side B (see ../README.md, Fixture 2).
// Declares `compute` returning `double`, conflicting with `tu_a.h`'s `int`.
double compute(int value);
