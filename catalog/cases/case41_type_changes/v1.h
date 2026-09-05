/* case41: Type-level changes (4 scenarios)
 *
 * 1. TYPE_REMOVED: entire struct removed from API
 * 2. TYPE_ADDED: new struct added (compatible)
 * 3. TYPE_ALIGNMENT_CHANGED: alignment requirement changed
 * 4. ENUM_LAST_MEMBER_VALUE_CHANGED: sentinel/boundary enum value changed
 *
 * abicheck detects: TYPE_REMOVED, TYPE_ADDED, TYPE_ALIGNMENT_CHANGED,
 *                   ENUM_LAST_MEMBER_VALUE_CHANGED
 */
#ifndef V1_H
#define V1_H

#ifdef __cplusplus
extern "C" {
#endif

/* Scenario 1: will be removed in v2 */
struct LegacyConfig {
    int mode;
    int flags;
};

/* Scenario 2: NewConfig doesn't exist yet — added in v2 */

/* Scenario 3: alignment will change */
struct __attribute__((aligned(8))) AlignedBuffer {
    char data[64];
};

/* Scenario 4: sentinel value will change */
typedef enum {
    PRIO_LOW    = 0,
    PRIO_MEDIUM = 1,
    PRIO_HIGH   = 2,
    PRIO_MAX    = 3   /* sentinel — will change to 4 in v2 */
} priority_t;

void process_config(struct LegacyConfig *cfg);
void fill_buffer(struct AlignedBuffer *buf);
void set_priority(priority_t p);

#ifdef __cplusplus
}
#endif
#endif
