#pragma once

// v1: uses the platform's native `long double` (80-bit x87 extended
// precision on x86-64 Linux, Itanium mangling `e`).
long double compute(long double x);
