### Fixed

- **The concept-declaration detector's exclusion for a type literally
  named `concept` is now general, not limited to brace-initialized
  variable templates.** A pre-C++20 header can shadow `concept` with a
  real type (`struct concept { concept(int); };`) and initialize a
  variable template of that type via *any* expression convertible to
  it — a converting constructor (`concept C = 1;`), not just aggregate
  init (`concept C = {};`) — so no per-initializer-shape check can ever
  be complete. The detector now instead checks whether `concept` is
  defined as a real type anywhere in the header and, if so, rejects every
  bare `concept NAME = ...` match in it outright.
