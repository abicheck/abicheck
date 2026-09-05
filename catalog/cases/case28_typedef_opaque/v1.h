/* case28: Typedef and opaque type changes (3 scenarios)
 *
 * 1. TYPEDEF_BASE_CHANGED: typedef changes underlying type
 *    typedef int dim_t → typedef long dim_t  (size: 4 → 8 bytes)
 *    BREAKING: all code using dim_t gets wrong size
 *
 * 2. TYPEDEF_REMOVED: typedef alias deleted
 *    Consumers using the alias get compile error
 *
 * 3. TYPE_BECAME_OPAQUE: complete struct → forward-declaration only
 *    Consumers can no longer stack-allocate the type
 *
 * abicheck detects: TYPEDEF_BASE_CHANGED, TYPEDEF_REMOVED, TYPE_BECAME_OPAQUE
 * ABICC equivalent: Typedef_BaseType, Type_Became_Opaque
 *
 * NOTE: Typedef tracking is critical for library CI (dimension typedefs, etc.)
 */
#ifndef V1_H
#define V1_H

#ifdef __cplusplus
extern "C" {
#endif

/* Scenario 1: typedef will change base type */
typedef int dim_t;

/* Scenario 2: typedef will be removed */
typedef unsigned int handle_t;

/* Scenario 3: complete struct will become opaque */
struct Context {
    int id;
    int flags;
    char name[32];
};

dim_t get_dimension(int axis);
handle_t create_handle(void);
struct Context *context_create(void);
void context_destroy(struct Context *ctx);

#ifdef __cplusplus
}
#endif
#endif
