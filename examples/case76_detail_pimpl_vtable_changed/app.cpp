// Consumer dispatches through the public `svm_algorithm`. The vtable
// layout it expects is fixed at v1 compile time; v2's reshuffle makes
// `status()` calls land on `progress()` (or vice versa) at runtime.
#include "v1.h"
#include <cstdio>

int main() {
    using namespace mylib;
    auto* a = mylib_make_svm();
    a->run();
    std::printf("status=%d (expect 1)\n", a->status());
    mylib_free_algo(a);
    return 0;
}
