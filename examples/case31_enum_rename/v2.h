/* case31 v2: Enum members renamed for clarity */
#ifndef V2_H
#define V2_H

typedef enum {
    LOG_NONE    = 0,
    LOG_ERROR   = 1,   /* was LOG_ERR */
    LOG_WARNING = 2,   /* was LOG_WARN */
    LOG_DEBUG   = 3,   /* was LOG_DBG */
    LOG_MAX     = 4
} log_level_t;

void set_log_level(log_level_t level);
extern log_level_t default_log_level;

#endif
