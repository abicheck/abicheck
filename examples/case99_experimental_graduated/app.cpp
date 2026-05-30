// case109 — consumer written against the experimental name.
//
// The v1-era consumer keeps compiling against v2 because the
// experimental alias is preserved alongside the stable name. The
// abicheck finding here is COMPATIBLE — informational, not breaking.
#include "v1.h"

int main() {
    lib::experimental::sort();
    lib::experimental::other_fn();
    return 0;
}
