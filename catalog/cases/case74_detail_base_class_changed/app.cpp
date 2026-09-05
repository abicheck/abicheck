// case74 — consumer of the public API.
//
// The user wrote against v1's `knn_descriptor`. They never named the
// internal `detail::descriptor_base` — yet a change to its layout makes
// stack-allocated `knn_descriptor` instances unsafe at v2 link-time.
#include "v1.h"
#include <cstdio>

int main() {
    using namespace mylib;

    // Stack-allocate the public type — its layout was determined by v1's
    // detail::descriptor_base. At v2 the base grew, so `neighbor_count_`
    // sits at a different offset and constructor stores it out of bounds.
    knn_descriptor d;
    std::printf("class_count   = %d (expect 2)\n", d.get_class_count());
    std::printf("neighbor_count = %d (expect 5)\n", d.get_neighbor_count());

    // Heap-allocated via factory: same break, dynamic.
    auto* d2 = mylib_make_descriptor();
    std::printf("factory class_count = %d (expect 2)\n", d2->get_class_count());
    mylib_free_descriptor(d2);
    return 0;
}
