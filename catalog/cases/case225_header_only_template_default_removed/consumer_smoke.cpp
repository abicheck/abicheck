// Independent witness: a consumer that names Box<char>, relying on U's default.
#include "v1.h"

int main() {
    mylib::Box<char> b{};
    (void)b;
    return 0;
}
