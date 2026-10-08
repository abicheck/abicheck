/* Witness: the consumer calls chan_open() with one argument, as v1 declared,
   and asserts v1's contract: opening a named channel returns fd 3.

   Under v2 the callee also reads a second argument the caller never
   supplies. What it finds there is whatever the caller's code last left in
   that slot -- a property of the caller's compiler and optimisation level,
   not of any contract, so the witness must not depend on it. On x86-64 the
   unset slot is %esi; the app pins it to a value outside v2's accepted
   `flags` set immediately before the call, making the outcome deterministic
   instead of relying on register residue (ASLR-free or not):
     v1: chan_open ignores %esi            -> fd 3,  exit 0
     v2: chan_open reads %esi as `flags`   -> fd -1, exit 1
   On other targets the slot is left as-is and the result may vary. */
#include "v1.h"
#include <stdio.h>

int main(void) {
#if defined(__x86_64__)
    __asm__ volatile("movl $0x7fff0000, %%esi" ::: "rsi");
#endif
    int fd = chan_open("demo");
    printf("chan_open -> %d (expected 3)\n", fd);
    if (fd != 3) {
        printf("BROKEN: the callee consumed an argument the caller never passed\n");
        return 1;
    }
    printf("chan_close -> %d\n", chan_close(fd));
    return 0;
}
