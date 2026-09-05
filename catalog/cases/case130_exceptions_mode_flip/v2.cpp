// Trivial public surface — identical in v1 and v2. The ABI difference this case
// demonstrates is in the *build mode* (captured in the per-side compile DB),
// not in the source or the emitted symbols.
int compute(int x);
int compute(int x) { return x + 1; }
