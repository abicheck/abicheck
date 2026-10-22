#include "v1.h"

int main() {
    // Default U = T from v1: Box<char> names Box<char, char>.
    mylib::Box<char> b{};
    (void)b;
    return 0;
}
