// Independent witness: a consumer that relies on Buf<char>'s default N = 4.
#include "v1.h"

static_assert(sizeof(mylib::Buf<char>) == 4, "Buf<char> is Buf<char, 4>");

int main() { return 0; }
