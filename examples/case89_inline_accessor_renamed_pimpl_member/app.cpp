// case89 — consumer compiled against v1.h. The inline body
// `return impl_->class_count_;` is baked into this app binary. When
// dynamically linked against the v2 library, descriptor_impl no longer
// has a `class_count_` field at the v1 offset — the consumer reads
// garbage or the wrong field.
#include "v1.h"
#include <cstdio>

int main() {
    mylib::descriptor d;
    std::printf("class_count = %d\n", d.get_class_count());
    return 0;
}
