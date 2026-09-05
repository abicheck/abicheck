#include "v2.h"

log_level_t default_log_level = LOG_ERROR;
static log_level_t current = LOG_NONE;
void set_log_level(log_level_t level) { current = level; }
