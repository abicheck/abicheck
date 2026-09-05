/* case40 v2: Field layout changes applied */
#ifndef V2_H
#define V2_H

#ifdef __cplusplus
extern "C" {
#endif

struct Packet {
    long version;        /* type changed: int → long */
    /* sequence REMOVED */
    int payload_size;    /* offset shifted (was after sequence, now after version) */
    unsigned flags : 8;  /* bitfield width changed: 4 → 8 */
    int priority;        /* new field appended (compatible for plain struct) */
};

int packet_send(struct Packet *pkt);

#ifdef __cplusplus
}
#endif
#endif
