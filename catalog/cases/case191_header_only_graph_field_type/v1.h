// case191 v1 — demo::Config has a single plain int field: zero
// TYPE_HAS_FIELD_TYPE edges of any demo:: kind, on purpose. Unlike case187
// (which gives Public a sibling Meta field so the L5 type-graph pass has a
// same-kind edge to confirm on *both* sides), this case relies on
// the L2 header-only graph's own confirmed-pass marker (extractor_passes.header_type_graph)
// to trust that "zero edges" really means "the pass ran and found none", not
// "the pass never ran" — see the README for why that distinction is safe here.
#pragma once

namespace demo {

struct Config {
    int value;
};

void fill_configs(Config* out, int n);
int use_config(const Config& c);

} // namespace demo
