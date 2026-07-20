// case191 v2 — demo::Config gains a field typed detail::RawConfig*, an
// internal type declared only in detail_private.h (never a --public-header).
// This grows Config's size (4 -> 16 bytes), which is a genuine binary-visible
// layout break on its own -- struct_size_changed/type_size_changed fire from
// the plain binary+header lane, no build integration needed. Layered on top,
// the L2 header-only graph (built automatically) proves the new demo::Config -> demo::detail::RawConfig
// TYPE_HAS_FIELD_TYPE edge and reports public_api_internal_dependency_added.
#pragma once

#include "detail_private.h"

namespace demo {

struct Config {
    int value;
    detail::RawConfig* raw;
};

void fill_configs(Config* out, int n);
int use_config(const Config& c);

} // namespace demo
