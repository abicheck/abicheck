#include "v1.h"

int main() {
    // v1 has no default, so the app spells both arguments.
    mylib::Box<char, int> b{};
    (void)b;
    return 0;
}
