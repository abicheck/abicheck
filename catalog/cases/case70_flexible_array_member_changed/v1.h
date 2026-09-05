#ifndef PACKET_H
#define PACKET_H

/* v1: packet with flexible array of float samples */
struct Packet {
    unsigned int id;
    unsigned int count;
    float data[];  /* flexible array member — float elements */
};

struct Packet *packet_create(unsigned int id, unsigned int count);
void packet_free(struct Packet *p);
float packet_sum(const struct Packet *p);

#endif
