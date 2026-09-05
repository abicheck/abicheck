#include "v1.h"
#include <cstdio>

int main() {
    mylib::auto_partitioner p{};
    auto* r = mylib::mylib_make_runner();
    std::printf("run(7) = %d (expect 14)\n", r->run(7, p));
    mylib::mylib_free_runner(r);
    return 0;
}
