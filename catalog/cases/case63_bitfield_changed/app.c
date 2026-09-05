/* DEMO: app compiled against v1 layout, but linked to v2 library at runtime.
   The app reads the priority field using v1 bit positions (bits 8-15).
   v2's regmap_init() writes priority=128 into bits 10-17 (v2 layout).
   The app reads bits 8-15 and gets a different value. */
#include "v1.h"
#include <stdio.h>
#include <string.h>

int main(void) {
    RegMap r;
    /* Zero-initialize to make corruption visible */
    memset(&r, 0, sizeof(r));

    /* v2 library writes fields using v2 bit positions */
    regmap_init(&r);

    /* App reads using v1 compiled layout — v1 priority is at bits 8-15,
       but v2 wrote priority=128 into bits 10-17 */
    uint32_t pri = r.priority;
    uint32_t mode = r.mode;
    uint32_t channel = r.channel;

    printf("mode     = %u (expected 2)\n", mode);
    printf("channel  = %u (expected 5)\n", channel);
    printf("priority = %u (expected 128)\n", pri);

    /* Show the raw word for debugging */
    uint32_t raw;
    memcpy(&raw, &r, sizeof(raw));
    printf("raw word = 0x%08X\n", raw);

    if (pri != 128) {
        printf("CORRUPTION: bitfield layout mismatch — app reads v1 bit "
               "positions but library wrote v2 bit positions!\n");
        return 1;
    }
    return 0;
}
