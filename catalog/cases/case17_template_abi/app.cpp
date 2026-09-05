/* DEMO: intentional ABI mismatch — v2 constructor writes 24 bytes into a
   16-byte slot, corrupting the adjacent sentinel member in the same struct. */
#include "v1.hpp"
#include <cstdio>
#include <cstring>
#include <new>

/* v1 layout: Buffer<int> = sizeof(T*) + sizeof(size_t) = 8+8 = 16 bytes on LP64 */
extern template class Buffer<int>;

int main() {
    /* Embed Buffer + sentinel in a struct — layout is guaranteed by C++ standard */
    struct Frame {
        unsigned char buf_storage[sizeof(Buffer<int>)]; /* 16 bytes per v1 */
        char          sentinel[8];
    } frame;

    std::memcpy(frame.sentinel, "SENTINEL", 8);

    std::printf("sizeof(Buffer<int>) at compile time = %zu\n",
                sizeof(Buffer<int>));
    std::printf("before ctor: sentinel = %.8s\n", frame.sentinel);

    /* Placement-new calls the constructor from the loaded .so.
       v1: writes 16 bytes (data_ + size_) — safe.
       v2: writes 24 bytes (data_ + size_ + capacity_) — hits sentinel. */
    Buffer<int>* b = new (frame.buf_storage) Buffer<int>(8);

    std::printf("after  ctor: sentinel = %.8s\n", frame.sentinel);

    if (std::memcmp(frame.sentinel, "SENTINEL", 8) != 0) {
        std::printf("CORRUPTION: v2 constructor wrote past Buffer slot!\n");
        b->~Buffer();
        return 1;
    }

    b->~Buffer();
    return 0;
}
