/* DEMO: the consumer allocates a Handle by value using v1's layout
   (4 bytes). The v2 constructor writes a vtable pointer plus the member into
   16 bytes, overrunning the consumer's stack slot. */
#include "v1.h"
#include <cstdio>
#include <cstring>

int main() {
    Handle *h = make_handle();
    std::printf("via factory: id() = %d (expected 7)\n", h->id());

    char canary[16];
    std::memset(canary, 'C', sizeof canary);
    Handle local;                 /* v1 layout: 4 bytes on the stack */
    char after[8] = "AFTER!!";
    (void)local;
    if (std::strcmp(after, "AFTER!!") != 0)
        std::printf("CORRUPTION: v2's vptr write overran the v1-sized slot\n");
    std::printf("after = %s\n", after);
    return 0;
}
