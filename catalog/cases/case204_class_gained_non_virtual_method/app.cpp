/* DEMO: the consumer keeps using the v1 members; the v2 addition is a new
   exported symbol it simply never calls, and the layout is untouched. */
#include "v1.h"
#include <cstdio>

int main() {
    Handle *h = make_handle();
    std::printf("via factory: id() = %d (expected 7)\n", h->id());
    Handle local;
    std::printf("local: id() = %d (expected 7)\n", local.id());
    return 0;
}
