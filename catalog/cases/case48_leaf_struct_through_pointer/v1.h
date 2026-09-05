/* case48: Leaf struct change propagated through pointer in function param
 *
 * A nested "leaf" struct changes layout. Public API takes Container* and
 * exposes leaf values via out-params (no by-value Leaf in signatures).
 * Callers compiled against v1 may pass a Container with old layout.
 *
 * BREAKING: TYPE_SIZE_CHANGED on Leaf propagates into Container layout.
 */
#ifndef CASE48_V1_H
#define CASE48_V1_H

typedef struct Leaf {
    short x;
    short y;
} Leaf;                  /* 4 bytes */

typedef struct Container {
    int  id;
    Leaf position;       /* embedded — offset 4 */
    int  flags;
} Container;

void  container_init(Container *c, int id, short x, short y);
void  container_get_pos(const Container *c, short *out_x, short *out_y);
int   container_flags(const Container *c);

#endif
