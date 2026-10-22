#include "v1.h"

int main() {
    // Default U = int from v1 in mylib::detail.
    mylib::detail::Box<char> b{};
    (void)b;
    return 0;
}
