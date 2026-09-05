#ifdef INCLUDE_V2
#include "v2.h"
#else
#include "v1.h"
#endif
#include <cstdio>

int main() {
    mylib::task_scheduler_init init(4);
    std::printf("active = %d (expect 1)\n", init.is_active() ? 1 : 0);
    init.terminate();
    std::printf("active = %d (expect 0)\n", init.is_active() ? 1 : 0);
    return 0;
}
