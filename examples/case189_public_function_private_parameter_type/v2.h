// case189 v2 — the previously-opaque `void*` parameter is given a real
// type: demo::detail::Options*, declared only in an internal, non-public
// header. Changing a parameter's type changes the function's mangled name
// (a genuinely different exported symbol — the old one disappears, a new
// one appears), a real, binary-visible break existing detectors correctly
// flag. public_api_internal_dependency_added fires alongside it, naming the
// internal type the new signature now depends on.
#pragma once

#include "detail_private.h"

namespace demo {

struct Meta {
    int m;
};

void configure(detail::Options* opaque, Meta info);

} // namespace demo
