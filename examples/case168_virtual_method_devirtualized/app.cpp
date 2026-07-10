/* DEMO: app compiled against v1, where flush() is virtual and occupies
   the vtable slot after encode(). v2 removed flush() from the vtable,
   so the app's virtual call through the old slot index dispatches to
   whatever now occupies that slot — reset(), which returns -1 and
   silently discards the pending count. */
#include "v1.h"
#include <cstdio>

int main() {
    Codec* c = codec_create();

    int a = c->encode(21);
    int b = c->encode(21);
    int flushed = c->flush(); /* virtual dispatch via the v1 slot index */

    std::printf("encode(21) = %d, %d (expected 42, 42)\n", a, b);
    std::printf("flush()    = %d (expected 2)\n", flushed);

    int broken = 0;
    if (flushed != 2) {
        std::printf("CORRUPTION: flush() dispatched to a different vtable "
                    "slot — the pending count was silently discarded!\n");
        broken = 1;
    }

    codec_destroy(c);
    return broken;
}
