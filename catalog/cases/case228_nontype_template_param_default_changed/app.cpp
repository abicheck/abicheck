#include "v1.h"

int main() {
    // Default N = 4 from v1: Buf<char> is char[4].
    mylib::Buf<char> b{};
    (void)b;
    return 0;
}
