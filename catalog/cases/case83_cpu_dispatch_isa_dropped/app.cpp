#include "v1.h"
#include <cstdio>

int main() {
    // Dispatcher caller — still works in v2.
    int a = mylib::kmeans_compute(10);
    // Pinned-ISA caller — explodes in v2 with unresolved symbol.
    int b = mylib::kmeans_compute_avx512(10);
    std::printf("dispatch=%d avx512=%d\n", a, b);
    return 0;
}
