#pragma once

// v2: the function's *purpose* and name are identical, but the library was
// rebuilt to use IEEE 754 quad precision (`__float128`, via libquadmath)
// instead of the platform's native `long double` -- the same kind of
// representation swap real toolchains perform for `long double` on ppc64
// (IBM double-double <-> IEEE binary128) or when opting into `__float128`
// for extended precision on x86/ARM. Source callers that spell the type as
// `long double` still compile against a header offering this signature only
// if the header itself was updated -- but the *mangled symbol* changes
// regardless of what any given caller's source says, because Itanium
// mangles `long double` as `e` and `__float128` as `g`. `__float128` is a
// GCC/Clang builtin type on x86-64 -- no header is required to *declare* it;
// `<quadmath.h>` (linked via `-lquadmath`) is only needed by v2.cpp for the
// `__float128` arithmetic operators the implementation actually uses.
__float128 compute(__float128 x);
