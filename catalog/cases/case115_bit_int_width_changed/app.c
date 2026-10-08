/* Witness: a consumer built against v1 owns a 64-bit-wide Accumulator,
   passes a _BitInt(64) delta and reads a _BitInt(64) result. The object is
   heap-allocated with v1's size and followed by a guard region of known
   bytes. Against v2 the library treats the same object as 128-bit storage
   (writing past the v1-sized object) and the delta/result travel through a
   different register/stack shape, so the value read back is wrong.
     v1: -1 + 5 wraps to 4, guard intact  -> exit 0
     v2: the carry lands in the guard      -> exit 1 */
#include "v1.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define GUARD_BYTES 32
#define GUARD_FILL 0xA5

int main(void) {
    unsigned char *mem = malloc(sizeof(Accumulator) + GUARD_BYTES);
    if (!mem) return 2;
    memset(mem, 0, sizeof(Accumulator));
    memset(mem + sizeof(Accumulator), GUARD_FILL, GUARD_BYTES);
    Accumulator *a = (Accumulator *)mem;

    /* Start at -1 so adding 5 carries out of the low 64 bits: v1 wraps to 4,
       while v2's 128-bit add propagates the carry into the upper half --
       which, for a v1-sized object, is the guard region. On x86-64 the
       upper half of a _BitInt(128) argument travels in %rdx, which a v1
       caller never sets; pin it to 0 so the outcome does not depend on
       register residue. */
    a->acc = -1;
#if defined(__x86_64__)
    __asm__ volatile("xorl %%edx, %%edx" ::: "rdx");
#endif
    acc_add(a, (_BitInt(64))5);
    long long v = (long long)acc_value(a);
    printf("acc_value = %lld (expected 4)\n", v);

    int status = (v == 4) ? 0 : 1;
    for (size_t i = 0; i < GUARD_BYTES; ++i) {
        if (mem[sizeof(Accumulator) + i] != GUARD_FILL) {
            printf("CORRUPTION: library wrote past the v1-sized Accumulator\n");
            status = 1;
            break;
        }
    }
    free(mem);
    return status;
}
