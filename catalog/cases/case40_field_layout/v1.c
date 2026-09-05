#include "v1.h"

/* v1 checksum uses original field layout */
int packet_send(struct Packet *pkt) {
    return pkt->version + pkt->sequence + pkt->payload_size + (int)pkt->flags;
}
