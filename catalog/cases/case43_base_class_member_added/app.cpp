#include "v1.hpp"
#include <cstdio>

int main() {
    Derived d;
    d.base_id = 41;
    d.value = 0;
    d.process();

    std::printf("value = %d\n", d.value);
    std::printf("expected = 42\n");

    if (d.value != 42) {
        std::printf("CORRUPTION: base-class layout changed, Derived::value offset mismatch\n");
        return 1;
    }
    return 0;
}
