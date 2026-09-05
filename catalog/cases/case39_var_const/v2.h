/* case39 v2: Variable const/removal changes applied */
#ifndef V2_H
#define V2_H

#ifdef __cplusplus
extern "C" {
#endif

/* Scenario 1: became const */
extern const int g_buffer_size;

/* Scenario 2: lost const */
extern int g_max_retries;

/* Scenario 3: g_legacy_flag REMOVED */

extern int get_config(void);

#ifdef __cplusplus
}
#endif
#endif
