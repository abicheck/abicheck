#include "v2.h"
const int g_buffer_size = 8192;
int g_max_retries = 5;
/* g_legacy_flag removed */
int get_config(void) { return g_buffer_size; }
