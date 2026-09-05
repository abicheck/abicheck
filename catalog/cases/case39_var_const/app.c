#include "v1.h"
#include <stdio.h>

int main(void) {
    printf("g_buffer_size  = %d\n", g_buffer_size);
    printf("g_max_retries  = %d\n", g_max_retries);
    printf("g_legacy_flag  = %d\n", g_legacy_flag);
    printf("get_config()   = %d\n", get_config());

    /* Attempt to write to g_buffer_size — legal with v1 (non-const) */
    g_buffer_size = 2048;
    printf("g_buffer_size after write = %d\n", g_buffer_size);

    return 0;
}
