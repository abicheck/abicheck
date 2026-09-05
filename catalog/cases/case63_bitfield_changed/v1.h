#ifndef REGMAP_H
#define REGMAP_H

#include <stdint.h>

/* Hardware register map — fields packed into 32-bit word */
typedef struct RegMap {
    uint32_t enable   : 1;   /* bit  0     */
    uint32_t mode     : 3;   /* bits 1-3   */
    uint32_t channel  : 4;   /* bits 4-7   */
    uint32_t priority : 8;   /* bits 8-15  */
    uint32_t reserved : 16;  /* bits 16-31 */
} RegMap;

void     regmap_init(RegMap *r);
uint32_t regmap_read_priority(const RegMap *r);
void     regmap_set_mode(RegMap *r, uint32_t mode);

#endif
