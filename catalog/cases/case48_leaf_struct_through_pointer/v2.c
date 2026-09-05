#include "v2.h"

void container_init(Container *c, int id, short x, short y) {
    c->id = id;
    c->position.x = x;
    c->position.y = y;
    c->position.z = 0;
    c->flags = 0;
}

void container_get_pos(const Container *c, short *out_x, short *out_y) {
    if (out_x) *out_x = c->position.x;
    if (out_y) *out_y = c->position.y;
}

int container_flags(const Container *c) { return c->flags; }
