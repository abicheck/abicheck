#include "v1.h"
#include <cstdio>

int main() {
    // Box<int> is exported by v1 as Box<int, int>.
    mylib::Box<int> b;
    std::printf("size = %d\n", b.size());
    return 0;
}
