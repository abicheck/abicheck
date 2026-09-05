/* case48 v2: Leaf gains extra field — Container layout shifts — BREAKING */
#ifndef CASE48_V2_H
#define CASE48_V2_H

typedef struct Leaf {
    short x;
    short y;
    int   z;             /* NEW: 4 extra bytes in Leaf */
} Leaf;                  /* 8 bytes — was 4 */

typedef struct Container {
    int  id;
    Leaf position;       /* now 8 bytes, was 4 → flags shifts by 4 bytes */
    int  flags;
} Container;

void  container_init(Container *c, int id, short x, short y);
void  container_get_pos(const Container *c, short *out_x, short *out_y);
int   container_flags(const Container *c);

#endif
