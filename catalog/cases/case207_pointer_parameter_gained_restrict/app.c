/* Witness: an unchanged old consumer passes two overlapping views of one
   buffer -- dst = buf + 1, src = buf -- which v1's declaration permits.
   Executed in order, blend() then computes a running prefix sum, so the
   app checks every element against that sequential oracle.

   Both libraries are built at -O3 (see CMakeLists.txt). v1's loop must
   honour possible aliasing; v2's `restrict` lets the optimizer vectorise
   it on the promise that dst and src never overlap, so the overlapping call
   reads stale src elements and the prefix sum comes out wrong. 128 elements
   are enough for both GCC and Clang to take the vectorised path.
     v1: buf[i] == 1 + 2 + ... + (i + 1)   -> exit 0
     v2: e.g. 1,3,5,7,9,... instead         -> exit 1 */
#include "v1.h"
#include <stdio.h>

#define N 128

int main(void) {
    float buf[N + 1];
    for (int i = 0; i <= N; i++) buf[i] = (float)(i + 1);

    blend(buf + 1, buf, N);

    int bad = -1;
    float expect = 0.0f;
    for (int i = 0; i <= N; i++) {
        expect += (float)(i + 1);
        if (buf[i] != expect) { bad = i; break; }
    }
    printf("first five: %.0f %.0f %.0f %.0f %.0f\n", buf[0], buf[1], buf[2], buf[3], buf[4]);
    if (bad >= 0) {
        printf("WRONG RESULT at element %d: overlapping call no longer honoured\n", bad);
        return 1;
    }
    return 0;
}
