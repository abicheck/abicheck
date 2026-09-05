// case79 — consumer instantiates descriptor<double>. Header says it's fine.
// Against v1: links. Against v2: undefined symbol at load time.
#include "v1.h"
#include <cstdio>

int main() {
    mylib::descriptor<double> d;
    d.set_threshold(0.5);
    std::printf("threshold = %f\n", d.threshold());
    return 0;
}
