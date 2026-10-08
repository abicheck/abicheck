/* Witness: the consumer passes (int 7, double 1.5) as v1 declared and checks
   what the callee received.

   On x86-64 System V the int travels in the first integer register and the
   double in the first SSE register *whatever their positions*, so v2's
   (double, int) callee still receives index=7 / value=1.5: argument delivery
   is intact on this ABI and the swap shows no runtime signal. That does not
   make the change compatible -- the two C declarations are incompatible, a
   recompiled caller silently converts 7 -> 7.0 and 1.5 -> 1 -- and on an ABI
   that assigns arguments positionally (Microsoft x64, i386 stack passing)
   the same swap delivers garbage (verified with ms_abi under GCC and Clang).
   The verdict rests on the changed parameter contract, not on this ABI's
   register assignment. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    plot_reset();
    plot_point(7, 1.5);
    int index = plot_last_index();
    double value = plot_last_value();
    printf("callee received index=%d value=%g (sent 7, 1.5)\n", index, value);
    return (index == 7 && value == 1.5) ? 0 : 1;
}
