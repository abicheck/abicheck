// case187 v2 — the previously-opaque `reserved` pointer is given a real type:
// demo::detail::PrivateType*, declared only in an internal, non-public
// header. The pointer's own storage is unchanged (still 8 bytes, same
// offset), but this is still a genuine binary-visible field-type change —
// old code that reads/writes `reserved` as `void*` now misinterprets the
// pointee — so type_field_type_changed correctly fires as BREAKING
// alongside the public_api_internal_dependency_added risk finding. The two
// findings are not redundant: the first says "this field's type changed",
// the second says "and the new type is one your consumers cannot see or
// track" — the risk survives even in a hypothetical where the field size
// happened not to change at all.
#pragma once

#include "detail_private.h"

namespace demo {

struct Meta {
    int m;
};

struct Public {
    int x;
    Meta meta;
    detail::PrivateType* reserved;
};

int use_public(const Public& p);

} // namespace demo
