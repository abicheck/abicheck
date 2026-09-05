/* case40: Field-level layout changes (5 scenarios in one struct)
 *
 * 1. TYPE_FIELD_REMOVED: field deleted from struct
 * 2. TYPE_FIELD_TYPE_CHANGED: field type changed (int → long)
 * 3. TYPE_FIELD_OFFSET_CHANGED: field offset shifted (from reorder)
 * 4. FIELD_BITFIELD_CHANGED: bitfield width changed
 * 5. TYPE_FIELD_ADDED_COMPATIBLE: field appended to plain struct
 *
 * These are the most common ABI breaks in practice — struct fields
 * shifting, changing type, or disappearing.
 *
 * abicheck detects: TYPE_FIELD_REMOVED, TYPE_FIELD_TYPE_CHANGED,
 *                   TYPE_FIELD_OFFSET_CHANGED, FIELD_BITFIELD_CHANGED,
 *                   TYPE_FIELD_ADDED_COMPATIBLE
 */
#ifndef V1_H
#define V1_H

#ifdef __cplusplus
extern "C" {
#endif

struct Packet {
    int version;         /* offset 0: type will change int→long in v2 */
    int sequence;        /* offset 4: will be removed in v2 */
    int payload_size;    /* offset 8: offset will shift when sequence removed */
    unsigned flags : 4;  /* bitfield: width will change 4→8 in v2 */
    /* no 'priority' field yet — will be added in v2 (compatible) */
};

int packet_send(struct Packet *pkt);

#ifdef __cplusplus
}
#endif
#endif
