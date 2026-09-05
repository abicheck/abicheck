#include "v1.hpp"
#include <stdio.h>

int main() {
    int bad = 0;

    ReorderDemo rd;
    rd.log_level = 1;
    rd.format = 2;
    rd.process();
    Logger *lg = &rd; lg->log("hello");
    Serializer *sr = &rd; sr->serialize("data");
    printf("ReorderDemo: log_level=%d format=%d\n", rd.log_level, rd.format);
    /* v1 postcondition: process()->log_level=10,format=20; log()->++log_level=11; serialize()->++format=21
     * v2 (Logger/Serializer swapped): this-ptr adjustments differ; writes land on wrong fields */
    if (!(rd.log_level == 11 && rd.format == 21)) {
        printf("BASE_ORDER_MISMATCH detected\n");
        bad = 1;
    }

    VirtualDemo vd;
    vd.log_level = 5;
    vd.init();
    printf("VirtualDemo: log_level=%d\n", vd.log_level);
    if (vd.log_level != 77) {
        printf("VIRTUAL_BASE_MISMATCH detected\n");
        bad = 1;
    }

    AddBaseDemo ad;
    ad.log_level = 3;
    ad.run();
    printf("AddBaseDemo: log_level=%d\n", ad.log_level);
    /* v2 AddBaseDemo gains Serializer base; run() also sets format=44.
     * With v1 binary (no format field), writing format=44 overflows the object. */
    if (ad.log_level != 33) {
        printf("ADDED_BASE_MISMATCH: log_level=%d (expected 33)\n", ad.log_level);
        bad = 1;
    }

    return bad ? 2 : 0;
}
