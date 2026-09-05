// case109 v2 — header-only policy rename, sibling of v1.cpp.
// Byte-identical to v1.cpp so the .so symbol table is unchanged across the
// version pair; the rename is purely in the header.
#include "v2.h"

namespace mylib { namespace flow {

extern "C" int mylib_flow_run(int x) {
    return x + 1;
}

}} // namespace mylib::flow
