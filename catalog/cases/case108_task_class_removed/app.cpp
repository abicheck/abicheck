#include "v1.h"
#include <cstdio>

int main() {
    mylib::task* t = mylib::mylib_spawn_dummy();
    t->set_ref_count(3);
    std::printf("ref_count after dec = %d (expect 2)\n", t->decrement_ref_count());
    delete t;
    return 0;
}
