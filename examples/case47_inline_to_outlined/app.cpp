#include "old/lib.hpp"
#include <cstdio>

int main() {
    Calculator c;
    int a = c.add(2, 3);
    int m = c.multiply(4, 5);
    int s = c.subtract(9, 1);

    std::printf("add=%d multiply=%d subtract=%d\n", a, m, s);
    std::printf("expected: 5 20 8\n");

    if (a != 5 || m != 20 || s != 8) {
        std::printf("UNEXPECTED: inline-to-outlined case should remain compatible\n");
        return 1;
    }
    return 0;
}
