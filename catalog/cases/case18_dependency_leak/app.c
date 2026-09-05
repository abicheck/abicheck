/* DEMO: intentional ABI mismatch — v2 process() writes to ThirdPartyHandle.y
   which is 4 bytes past the v1 allocation boundary. Educational only. */
#include "foo_v1.h"
#include <stdio.h>
#include <string.h>

/* Use a struct to guarantee canary sits immediately after h */
typedef struct {
    ThirdPartyHandle h;  /* v1: 4 bytes; v2 reads 8 bytes here */
    int canary;          /* sits at offset sizeof(ThirdPartyHandle) */
} TestFrame;

int main(void) {
    TestFrame frame;
    frame.h.x  = 42;
    frame.canary = 0x5AFE5AFE;

    printf("sizeof(ThirdPartyHandle) = %zu  (v1: 4, v2 reads 8)\n",
           sizeof(frame.h));
    printf("before process: h.x=%d  canary=0x%X\n",
           frame.h.x, (unsigned)frame.canary);

    process(&frame.h);

    printf("after  process: h.x=%d  canary=0x%X\n",
           frame.h.x, (unsigned)frame.canary);

    if (frame.canary != 0x5AFE5AFE) {
        printf("CORRUPTION: v2 read/wrote past ThirdPartyHandle boundary!\n");
        return 1;
    }

    return 0;
}
