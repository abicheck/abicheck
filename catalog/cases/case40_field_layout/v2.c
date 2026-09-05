#include "v2.h"

/* v2 interprets the same memory using changed layout */
int packet_send(struct Packet *pkt) {
    long v = pkt->version;
    /* pkt->priority is at offset 16+ in v2 layout, but the caller (compiled with v1)
     * only allocated ~16 bytes for the struct. This intentional OOB read is the demo:
     * it reads stack bytes past the v1 struct, producing a different sum -> mismatch. */
    int sum = (int)(v & 0xFFFF) + pkt->payload_size + (int)pkt->flags + pkt->priority;
    return sum;
}
