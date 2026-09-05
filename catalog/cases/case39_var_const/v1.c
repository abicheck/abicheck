#include "v1.h"
int g_buffer_size = 4096;
const int g_max_retries = 3;
int g_legacy_flag = 1;
int get_config(void) { return g_buffer_size; }
