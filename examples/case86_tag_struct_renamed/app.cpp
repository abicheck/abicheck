#include "v1.h"
#include <cstdio>

int main() {
    mylib::descriptor<mylib::method::brute_force, mylib::task::classification> d;
    std::printf("kind = %d\n", d.kind());
    return 0;
}
