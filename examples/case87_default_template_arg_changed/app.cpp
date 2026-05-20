#include "v1.h"
#include <cstdio>

int main() {
    mylib::descriptor<float> d;   // default Distance from v1 header
    std::printf("dim = %d\n", d.dimension());
    return 0;
}
