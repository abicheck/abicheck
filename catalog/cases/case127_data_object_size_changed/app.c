#include <stdio.h>
#include "v1.h"

/* App compiled against v1: the linker reserves a copy relocation for
 * config_table sized at CONFIG_SLOTS (16) * sizeof(int) = 64 bytes in the
 * executable's BSS. The app reads the last slot it knows about. */
int main(void)
{
    config_table[CONFIG_SLOTS - 1] = 99;
    int v = config_get(CONFIG_SLOTS - 1);
    printf("config_table[%d] = %d\n", CONFIG_SLOTS - 1, v);
    return v == 99 ? 0 : 1;
}
