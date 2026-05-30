/* Consumer built against v1 passes/reads a 64-bit _BitInt; against v2 the
   ABI expects 128-bit storage, so arguments and the returned value are
   miscompiled. */
#include "v1.h"

int main(void) {
    Accumulator a = {0};
    acc_add(&a, (_BitInt(64))5);
    return (int)acc_value(&a);
}
