// G32 Phase 0 "ODR conflict" fixture, side A (see ../README.md, Fixture 2).
// `compute` is declared returning `int` here and `double` in `tu_b.h` —
// a genuine cross-TU conflict a correct multi-TU merge (Phase C) must
// reject rather than silently pick one side.
int compute(int value);
