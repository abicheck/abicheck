// Independent witness: a consumer that relies on detail::Box<char>'s default U = int.
#include "v1.h"
#include <type_traits>

static_assert(std::is_same<decltype(mylib::detail::Box<char>{}.u), int>::value,
              "detail::Box<char> names detail::Box<char, int>");

int main() { return 0; }
