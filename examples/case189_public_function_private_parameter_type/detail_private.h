// case189 — internal-only header. Never passed as the public --public-header
// root, so abicheck's L5 source graph classifies demo::detail::Options as
// non-public: any public declaration that comes to depend on it is a
// public_api_internal_dependency_added risk, not a documented API surface.
#pragma once

namespace demo::detail {

struct Options {
    int y;
};

} // namespace demo::detail
