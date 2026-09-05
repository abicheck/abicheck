/* case31: Enum member rename — same value, different name
 *
 * The library renames enum members while keeping their integer values.
 * This is a SOURCE-LEVEL break: code using the old names won't compile.
 * Binary compatibility is preserved (enum values unchanged).
 *
 * abicheck detects: ENUM_MEMBER_RENAMED (+ ENUM_MEMBER_REMOVED for old names)
 *
 * ABICC equivalent: Enum_Member_Name
 */
#ifndef V1_H
#define V1_H

typedef enum {
    LOG_NONE    = 0,
    LOG_ERR     = 1,   /* renamed to LOG_ERROR in v2 */
    LOG_WARN    = 2,   /* renamed to LOG_WARNING in v2 */
    LOG_DBG     = 3,   /* renamed to LOG_DEBUG in v2 */
    LOG_MAX     = 4
} log_level_t;

void set_log_level(log_level_t level);
extern log_level_t default_log_level;

#endif
