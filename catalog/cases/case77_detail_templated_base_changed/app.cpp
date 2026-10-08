// case77 — consumer that uses only the public templated descriptor.
//
// The consumer never names ``detail::descriptor_base`` directly; it only
// uses ``knn_descriptor<task::classification>``. A change to the
// *templated* internal base still propagates into every instantiation of
// the public class.
//
// Witness: heap-allocate a v1-sized object followed by a guard region of
// known bytes and placement-construct it (the constructor is the library's
// explicit instantiation, so v2's runs after the swap). v2 stores
// `neighbor_count_` past the v1-sized object, corrupting the guard
// deterministically.
//   v1: class_count=2 neighbor_count=5, guard intact -> exit 0
//   v2: guard corrupted / wrong values               -> exit 1
#include "v1.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>

int main() {
    using namespace mylib;
    using Desc = knn_descriptor<task::classification>;
    constexpr std::size_t kGuard = 32;
    constexpr unsigned char kFill = 0xA5;
    auto* mem = static_cast<unsigned char*>(std::malloc(sizeof(Desc) + kGuard));
    if (!mem) return 2;
    std::memset(mem + sizeof(Desc), kFill, kGuard);

    auto* d = new (mem) Desc();
    int cc = d->get_class_count();
    int nc = d->get_neighbor_count();
    std::printf("sizeof(knn_descriptor) as compiled = %zu\n", sizeof(Desc));
    std::printf("class_count    = %d (expect 2)\n", cc);
    std::printf("neighbor_count = %d (expect 5)\n", nc);

    int status = (cc == 2 && nc == 5) ? 0 : 1;
    for (std::size_t i = 0; i < kGuard; ++i) {
        if (mem[sizeof(Desc) + i] != kFill) {
            std::printf("CORRUPTION: constructor wrote past the v1-sized object\n");
            status = 1;
            break;
        }
    }
    d->~Desc();
    std::free(mem);
    return status;
}
