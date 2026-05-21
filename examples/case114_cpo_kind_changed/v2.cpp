#include "v2.h"

namespace lib {

// `sort` itself is `inline constexpr` in the header, which gives it
// linkage without any out-of-line definition. Only the operator() body
// needs to be defined here so the symbol is exported by the .so.
void __sort_fn::operator()(int*, int*) const {}

} // namespace lib
