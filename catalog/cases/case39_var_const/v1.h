/* case39: Global variable const qualifier + removal (3 scenarios)
 *
 * 1. VAR_BECAME_CONST: non-const → const
 *    Old binaries writing to it get SIGSEGV (data moves to .rodata)
 *
 * 2. VAR_LOST_CONST: const → non-const
 *    Old binaries may have inlined the const value at compile time (ODR break)
 *
 * 3. VAR_REMOVED: public variable removed entirely
 *    Old binaries referencing it get undefined symbol at load
 *
 * abicheck detects: VAR_BECAME_CONST, VAR_LOST_CONST, VAR_REMOVED
 * ABICC equivalent: Global_Data_Became_Const, Global_Data_Removed_Const
 */
#ifndef V1_H
#define V1_H

#ifdef __cplusplus
extern "C" {
#endif

/* Scenario 1: will become const in v2 */
extern int g_buffer_size;

/* Scenario 2: const, will lose const in v2 */
extern const int g_max_retries;

/* Scenario 3: will be removed in v2 */
extern int g_legacy_flag;

extern int get_config(void);

#ifdef __cplusplus
}
#endif
#endif
