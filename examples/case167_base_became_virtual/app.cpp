/* DEMO: app compiled against v1 (non-virtual base: bytes_moved @16).
   v2 makes Device a virtual base, which moves bytes_moved to @8 and
   places the Device subobject at the end of the object. The app's
   direct field access still uses offset 16 — where v2 now stores the
   virtual-base vptr — so it reads a pointer value as a byte count. */
#include "v1.h"
#include <cstdio>

int main() {
    Stream* s = stream_create(3);

    long via_lib = stream_bytes(s); /* library code: always correct layout */
    long direct  = s->bytes_moved;  /* v1 offset 16: vbase vptr under v2!  */

    std::printf("bytes via library = %ld (expected 4096)\n", via_lib);
    std::printf("bytes direct      = %ld (expected 4096)\n", direct);

    int broken = 0;
    if (direct != via_lib) {
        std::printf("CORRUPTION: direct access used the v1 (non-virtual base) "
                    "offset and read the virtual-base machinery!\n");
        broken = 1;
    }

    stream_destroy(s);
    return broken;
}
