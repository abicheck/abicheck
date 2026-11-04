// Independent witness: a consumer that relies on Box<char>'s default U = int.
#include "v1.h"
#include <type_traits>

static_assert(std::is_same<decltype(mylib::Box<char>{}.u), int>::value,
              "Box<char> names Box<char, int>");

int main() { return 0; }
