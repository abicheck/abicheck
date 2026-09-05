/* case28 v2: typedef/opaque changes applied */
#ifndef V2_H
#define V2_H

#ifdef __cplusplus
extern "C" {
#endif

/* Scenario 1: typedef base type changed (int → long) */
typedef long dim_t;

/* Scenario 2: handle_t typedef REMOVED — consumers must use unsigned int directly */

/* Scenario 3: struct became opaque (forward-decl only) */
struct Context;

dim_t get_dimension(int axis);
/* handle_t create_handle(void);  -- removed with the typedef */
unsigned int create_handle(void);
struct Context *context_create(void);
void context_destroy(struct Context *ctx);

#ifdef __cplusplus
}
#endif
#endif
