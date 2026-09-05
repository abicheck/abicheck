#include "v2.h"

void regmap_init(RegMap *r) {
    r->enable   = 1;
    r->mode     = 2;
    r->channel  = 5;
    r->priority = 128;
    r->reserved = 0;
}

uint32_t regmap_read_priority(const RegMap *r) {
    return r->priority;
}

void regmap_set_mode(RegMap *r, uint32_t mode) {
    r->mode = mode & 0x1F;  /* 5-bit mask now */
}
