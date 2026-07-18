// case188 — internal-only header. Never passed as the public --public-header
// root, so abicheck's L5 source graph classifies demo::detail::InternalBase
// as non-public: any public declaration that comes to depend on it is a
// public_api_internal_dependency_added risk, not a documented API surface.
#pragma once

namespace demo::detail {

struct InternalBase {
    int y;
};

} // namespace demo::detail
