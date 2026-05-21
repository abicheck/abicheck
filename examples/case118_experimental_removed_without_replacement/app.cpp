// case110 — consumer written against the experimental name.
//
// Against v1 this compiles; against v2 the call site is a hard error
// — the experimental name is gone and nothing took its place.
#include "v1.h"

int main() {
    lib::experimental::bar();
    return 0;
}
