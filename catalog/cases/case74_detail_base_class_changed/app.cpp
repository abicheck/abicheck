// case74 — consumer of the public API.
//
// The user wrote against v1's `knn_descriptor`. They never named the
// internal `detail::descriptor_base`, yet a change to its layout makes every
// consumer-allocated `knn_descriptor` too small for the v2 constructor.
//
// Witness: the consumer heap-allocates a v1-sized object followed by a guard
// region of known bytes and placement-constructs it, which runs the
// library's (v2, after the swap) constructor. v2 stores `neighbor_count_` at
// its new, larger offset -- past the v1-sized object -- so the guard is
// corrupted deterministically, and the accessor reads back the wrong field.
//   v1: class_count=2 neighbor_count=5, guard intact -> exit 0
//   v2: guard corrupted / wrong values               -> exit 1
#include "v1.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>

int main() {
    using namespace mylib;
    constexpr std::size_t kGuard = 32;
    constexpr unsigned char kFill = 0xA5;
    auto* mem = static_cast<unsigned char*>(std::malloc(sizeof(knn_descriptor) + kGuard));
    if (!mem) return 2;
    std::memset(mem + sizeof(knn_descriptor), kFill, kGuard);

    auto* d = new (mem) knn_descriptor();
    int cc = d->get_class_count();
    int nc = d->get_neighbor_count();
    std::printf("sizeof(knn_descriptor) as compiled = %zu\n", sizeof(knn_descriptor));
    std::printf("class_count    = %d (expect 2)\n", cc);
    std::printf("neighbor_count = %d (expect 5)\n", nc);

    int status = (cc == 2 && nc == 5) ? 0 : 1;
    for (std::size_t i = 0; i < kGuard; ++i) {
        if (mem[sizeof(knn_descriptor) + i] != kFill) {
            std::printf("CORRUPTION: constructor wrote past the v1-sized object\n");
            status = 1;
            break;
        }
    }
    d->~knn_descriptor();
    std::free(mem);
    return status;
}
