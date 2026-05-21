// case109 v1 — header-only policy rename. The .cpp deliberately does not
// instantiate `function_node<queueing>` because that would bake the policy
// type into a weak template-instantiation symbol and produce a FUNC_REMOVED
// finding between v1 and v2, undermining the "no exported symbol change"
// claim. v1.cpp and v2.cpp are byte-identical; only the headers differ.
#include "v1.h"

namespace mylib { namespace flow {

extern "C" int mylib_flow_run(int x) {
    return x + 1;
}

}} // namespace mylib::flow
