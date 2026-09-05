/* case41 v2: Type-level changes applied */
#ifndef V2_H
#define V2_H

#ifdef __cplusplus
extern "C" {
#endif

/* Scenario 1: LegacyConfig REMOVED */

/* Scenario 2: NewConfig ADDED */
struct NewConfig {
    int mode;
    int flags;
    int version;
};

/* Scenario 3: alignment changed (8 → 64) */
struct __attribute__((aligned(64))) AlignedBuffer {
    char data[64];
};

/* Scenario 4: sentinel value changed (3 → 4) */
typedef enum {
    PRIO_LOW    = 0,
    PRIO_MEDIUM = 1,
    PRIO_HIGH   = 2,
    PRIO_URGENT = 3,   /* new member inserted before sentinel */
    PRIO_MAX    = 4    /* sentinel changed: was 3, now 4 */
} priority_t;

/* process_config removed with LegacyConfig */
void fill_buffer(struct AlignedBuffer *buf);
void set_priority(priority_t p);

#ifdef __cplusplus
}
#endif
#endif
