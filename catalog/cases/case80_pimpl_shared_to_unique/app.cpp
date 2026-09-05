#include "v1.h"
#include <cstdio>

int main() {
    mylib::descriptor d;
    d.set_class_count(7);
    std::printf("class_count = %d (expect 7)\n", d.get_class_count());
    return 0;
}
