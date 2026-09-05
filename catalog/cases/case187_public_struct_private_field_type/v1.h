// case187 v1 — demo::Public depends only on the public demo::Meta type; its
// third member is an opaque `void*` reserved for future use.
//
// Meta's own field (Public -> Meta) exists so the L5 type-graph pass has a
// TYPE_HAS_FIELD_TYPE edge to confirm on *both* v1 and v2 (a coverage-honesty
// requirement: without at least one real edge of that kind on the old side
// too, abicheck cannot distinguish "the pass ran and found nothing" from
// "the pass never ran at all", and correctly declines to report anything).
#pragma once

namespace demo {

struct Meta {
    int m;
};

struct Public {
    int x;
    Meta meta;
    void* reserved;
};

int use_public(const Public& p);

} // namespace demo
