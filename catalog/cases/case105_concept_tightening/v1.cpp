#include "v1.h"

namespace mylib {

// Explicit instantiation: the library ships sum<int> as a real symbol.
// Concepts are checked at instantiation time, so this TU verifies that
// `int` satisfies v1's `Addable`.
template <Addable T>
T sum(T a, T b) {
    return a + b;
}

template int sum<int>(int, int);

} // namespace mylib
