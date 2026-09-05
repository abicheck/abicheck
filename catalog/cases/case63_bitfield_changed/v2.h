#ifndef REGMAP_H
#define REGMAP_H

#include <stdint.h>

/* Hardware register map — 'mode' widened from 3 to 5 bits to support new modes.
   This shifts channel, priority, and reserved fields to different bit positions. */
typedef struct RegMap {
    uint32_t enable   : 1;   /* bit  0       (unchanged) */
    uint32_t mode     : 5;   /* bits 1-5     (was 3 bits → now 5 bits!) */
    uint32_t channel  : 4;   /* bits 6-9     (was 4-7, shifted +2) */
    uint32_t priority : 8;   /* bits 10-17   (was 8-15, shifted +2) */
    uint32_t reserved : 14;  /* bits 18-31   (was 16 bits → now 14) */
} RegMap;

void     regmap_init(RegMap *r);
uint32_t regmap_read_priority(const RegMap *r);
void     regmap_set_mode(RegMap *r, uint32_t mode);

#endif
