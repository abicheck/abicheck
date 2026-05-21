#include "v2.h"

namespace lib {

void __sort_fn::operator()(int*, int*) const {}

// Out-of-line definition of the CPO variable so it has a concrete
// address (avoids the inline-constexpr storage gymnastics that some
// platforms handle differently).
constexpr __sort_fn sort;

} // namespace lib
