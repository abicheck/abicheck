#include "v1.h"
#include <stdio.h>
#include <string.h>

int main(void) {
    /* Use LegacyConfig (removed in v2) */
    struct LegacyConfig cfg;
    cfg.mode  = 1;
    cfg.flags = 0xFF;
    printf("process_config(mode=%d, flags=%d)\n", cfg.mode, cfg.flags);
    process_config(&cfg);

    /* Use AlignedBuffer (alignment changes in v2) */
    struct AlignedBuffer buf;
    memset(buf.data, 'A', sizeof(buf.data));
    printf("fill_buffer (alignof=%zu, sizeof=%zu)\n",
           _Alignof(struct AlignedBuffer), sizeof(struct AlignedBuffer));
    fill_buffer(&buf);

    /* Use priority enum (sentinel value changes in v2) */
    printf("set_priority(PRIO_HIGH=%d)\n", PRIO_HIGH);
    set_priority(PRIO_HIGH);

    printf("PRIO_MAX = %d (sentinel)\n", PRIO_MAX);

    return 0;
}
