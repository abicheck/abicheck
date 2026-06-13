/* Consumer built against v1 treats counter::value as a plain int. Against v2 it
   is _Atomic(int), whose size and alignment may differ, so the struct layout and
   access ABI can diverge. */
#include "v1.h"

int main(void) {
    struct counter c = {0};
    return get_count(&c) == 0 ? 0 : 1;
}
