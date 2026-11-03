// case122 consumer witness: names the exact instantiation type.
//
// A direct call such as `clamp<int>(5, 0, 10)` compiles against both headers
// (int converts to long), so it does not prove a break. Taking the address as
// a specific function-pointer type does: against v1 `&clamp<int>` has type
// `int (*)(int, int, int)`; against v2 it is `int (*)(int, long, long)` and
// the initialization is ill-formed. The library binary is identical either
// way -- this is a source/API break, not an ABI break of the library.
#include "v1.h"

int (*const clamp_int)(int, int, int) = &clamp<int>;

int main() { return clamp_int(5, 0, 10) == 5 ? 0 : 1; }
