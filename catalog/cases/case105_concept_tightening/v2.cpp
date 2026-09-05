#include "v2.h"

namespace mylib {

// Same explicit instantiation as v1: `int` still satisfies the
// tightened concept (it is default-constructible and addable), so the
// library keeps the same exported symbol `_ZN5mylib3sumIiEET_S1_S1_`.
// The break is at the *consumer* call site — any consumer that
// instantiates `sum<T>` with a non-default-constructible `T` no
// longer compiles.
template <Addable T>
T sum(T a, T b) {
    return a + b;
}

template int sum<int>(int, int);

} // namespace mylib
