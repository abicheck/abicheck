/* case33: Pointer indirection level changes
 *
 * Binary ABI break: changing T* to T** (or vice versa) means the caller
 * passes/receives the wrong level of indirection → immediate crash or
 * silent memory corruption.
 *
 * Two scenarios:
 * 1. Parameter: process(int* data) → process(int** data)
 * 2. Return type: int* get_buffer() → int** get_buffer()
 *
 * abicheck detects: PARAM_POINTER_LEVEL_CHANGED, RETURN_POINTER_LEVEL_CHANGED
 * ABICC equivalent: Parameter_PointerLevel_Increased, Return_PointerLevel_Increased
 */
#ifndef V1_H
#define V1_H

#ifdef __cplusplus
extern "C" {
#endif

void process(int *data);
int *get_buffer(void);

#ifdef __cplusplus
}
#endif
#endif
