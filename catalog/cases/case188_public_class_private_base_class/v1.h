// case188 v1 — demo::PublicHandle inherits only from the public demo::Base.
//
// The Base inheritance edge exists so the L5 type-graph pass has a
// TYPE_INHERITS edge to confirm on *both* v1 and v2 (a coverage-honesty
// requirement: without at least one real edge of that kind on the old side
// too, abicheck cannot distinguish "the pass ran and found nothing" from
// "the pass never ran at all", and correctly declines to report anything).
#pragma once

namespace demo {

struct Base {
    int b;
};

struct PublicHandle : Base {
    int x;
};

int use_handle(const PublicHandle& h);

} // namespace demo
