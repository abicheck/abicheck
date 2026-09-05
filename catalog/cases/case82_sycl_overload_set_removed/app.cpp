#include "v1.h"
#include <cstdio>

int main() {
    mylib::descriptor d;
    mylib::table t;
    sycl::queue q;
    auto r1 = mylib::compute(d, t);
    auto r2 = mylib::compute(q, d, t);   // <-- requires SYCL overload
    std::printf("cpu=%d gpu=%d\n", r1.code, r2.code);
    return 0;
}
