#include "v1.h"

int main() {
    // Default U = int from v1: Box<char> names Box<char, int>.
    mylib::Box<char> b{};
    (void)b;
    return 0;
}
