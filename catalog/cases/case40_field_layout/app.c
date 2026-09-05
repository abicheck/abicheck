#include "v1.h"
#include <stdio.h>
#include <string.h>

int main(void) {
    struct Packet pkt;
    memset(&pkt, 0, sizeof(pkt));  /* zero all fields + padding for deterministic checksum */

    pkt.version      = 1;
    pkt.sequence     = 42;
    pkt.payload_size = 1024;
    pkt.flags        = 0xF;

    int expected = pkt.version + pkt.sequence + pkt.payload_size + (int)pkt.flags;
    int got = packet_send(&pkt);

    printf("sizeof(Packet) = %zu\n", sizeof(pkt));
    printf("expected checksum (v1 layout) = %d\n", expected);
    printf("packet_send()                = %d\n", got);

    if (got != expected) {
        printf("LAYOUT_MISMATCH: library interpreted struct with incompatible field layout\n");
        return 2;
    }

    return 0;
}
