/* Witness: the consumer allocates a Handle itself, using v1's layout
   (4 bytes), followed by a guard region of known bytes, and placement-
   constructs it -- which runs the library's constructor. v2's constructor
   stores a vtable pointer at offset 0 and id_ at offset 8, i.e. 12 bytes
   into a 4-byte object, so the guard is corrupted deterministically (no
   reliance on stack layout or stack canaries).
     v1: id()=7, guard intact      -> exit 0
     v2: guard corrupted / wrong id -> exit 1 */
#include "v1.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>

int main() {
    Handle *h = make_handle();
    std::printf("via factory: id() = %d (expected 7)\n", h->id());

    constexpr std::size_t kGuard = 32;
    constexpr unsigned char kFill = 0xA5;
    auto *mem = static_cast<unsigned char *>(std::malloc(sizeof(Handle) + kGuard));
    if (!mem) return 2;
    std::memset(mem + sizeof(Handle), kFill, kGuard);

    Handle *local = new (mem) Handle();
    int id = local->id();
    std::printf("sizeof(Handle) as compiled = %zu, local.id() = %d (expected 7)\n",
                sizeof(Handle), id);

    int status = (id == 7) ? 0 : 1;
    for (std::size_t i = 0; i < kGuard; ++i) {
        if (mem[sizeof(Handle) + i] != kFill) {
            std::printf("CORRUPTION: v2's constructor overran the v1-sized object\n");
            status = 1;
            break;
        }
    }
    std::free(mem);
    return status;
}
