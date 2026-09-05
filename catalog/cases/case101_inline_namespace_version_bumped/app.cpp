// case112 — consumer spells lib::sort / lib::unique without naming the
// inline namespace. Source compiles against v1 and v2 unchanged; the
// failure is at link time when an old TU and a new TU end up in the
// same program.
#include "v1.h"

int main() {
    lib::sort();
    lib::unique();
    return 0;
}
