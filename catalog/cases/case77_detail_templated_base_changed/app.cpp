// case77 — consumer that uses only the public templated descriptor.
//
// The consumer never names ``detail::descriptor_base`` directly; it only
// uses ``knn_descriptor<task::classification>``. A change to the
// *templated* internal base still propagates into every instantiation
// of the public class.
#include "v1.h"
#include <cstdio>

int main() {
    using namespace mylib;
    knn_descriptor<task::classification> d;
    std::printf("class_count    = %d (expect 2)\n", d.get_class_count());
    std::printf("neighbor_count = %d (expect 5)\n", d.get_neighbor_count());

    auto* d2 = mylib_make_classification();
    std::printf("factory class_count = %d (expect 2)\n", d2->get_class_count());
    mylib_free_classification(d2);
    return 0;
}
