/* DEMO: intentional ABI mismatch — v2 Buffer constructor writes 128 bytes into
   a 64-byte stack slot, corrupting adjacent memory. Educational only. */
#include "v1.h"
#include <cstdio>

int main() {
    /* Scenario 1: via factory — shows size() mismatch */
    Buffer* b = make_buffer();
    std::printf("via factory: size() = %d (expected 64)\n", b->size());
    delete b;

    /* Scenario 2: by value on stack — v2 constructor writes 128 bytes into
     * a 64-byte slot, overwriting adjacent stack memory (ASAN detects this) */
    {
        char canary[8] = "CANARY!";
        Buffer local_buf;          /* v1 layout: 64 bytes on stack */
        char after[8]  = "AFTER!!";
        (void)local_buf;
        std::printf("canary = %s\n", canary);
        std::printf("after  = %s\n", after);
        if (__builtin_strcmp(after, "AFTER!!") != 0)
            std::printf("CORRUPTION: stack overwritten by v2 constructor!\n");
    }
    return 0;
}
