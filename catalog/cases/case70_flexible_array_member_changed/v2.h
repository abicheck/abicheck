#ifndef PACKET_H
#define PACKET_H

/* v2: flexible array element type changed from float to double.
   sizeof(element) doubles from 4 to 8 bytes. Consumers that allocated
   with count * sizeof(float) now have half the space needed, and
   consumers that index data[i] read at wrong byte offsets. */
struct Packet {
    unsigned int id;
    unsigned int count;
    double data[];  /* FAM element type changed: float → double */
};

struct Packet *packet_create(unsigned int id, unsigned int count);
void packet_free(struct Packet *p);
double packet_sum(const struct Packet *p);

#endif
