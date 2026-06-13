// Consumer compiled against the v1 header. It upcasts a Widget* to its Payload
// base and reads Payload::value through that pointer — exactly the operation
// whose offset the Empty Base Optimization decides. The cast offset is baked in
// at *compile time*: with the v1 library Payload sits at offset 0; load the v2
// library underneath the same binary and Payload has moved to offset 8, so the
// read returns Tag::state (0) instead of the real value (42).
#include "v1.h"
#include <cstdio>

int main() {
    Widget* w = make_widget();

    // Upcast uses the compile-time base-subobject offset (0 under v1).
    const Payload* p = w;
    long via_base = p->value;
    long via_accessor = widget_payload_value(w);  // library-side, always correct

    std::printf("Payload::value via base cast = %ld (expected 42)\n", via_base);
    std::printf("Payload::value via accessor  = %ld (expected 42)\n", via_accessor);

    delete w;

    if (via_base != 42) {
        std::printf("CORRUPTION: base-subobject offset shifted (EBO lost)\n");
        return 1;
    }
    return 0;
}
