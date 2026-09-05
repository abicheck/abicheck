#include "core.h"
// core_mul deliberately removed in v2 — the bundle no longer provides it.
// libalgo still imports it; runtime load of libalgo.so will fail.
extern "C" int core_add(int a, int b) { return a + b; }
