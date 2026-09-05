/* case30: Field qualifier changes (const, volatile)
 *
 * Demonstrates two field qualifier scenarios on C struct fields:
 * - A field gains const (callers can no longer write to it directly)
 * - A field gains volatile (compiler must re-read from memory on every access)
 *
 * Note: `mutable` is a C++ keyword only — it has no meaning in C structs.
 * This is a pure C example; all three fields are plain `int` in v1.
 *
 * Binary layout: UNCHANGED — qualifiers are semantic/source-level only.
 * abicheck detects: STRUCT_FIELD_TYPE_CHANGED (castxml reports "int" vs "const int"/"volatile int"
 *   as distinct type names → BREAKING via struct field type diff).
 */
#ifndef V1_H
#define V1_H

#ifdef __cplusplus
extern "C" {
#endif

struct SensorConfig {
    int   sample_rate;    /* will become const in v2 */
    int   raw_value;      /* will become volatile in v2 */
    int   cache_hits;     /* unchanged across versions */
};

int sensor_read(struct SensorConfig *cfg);

#ifdef __cplusplus
}
#endif
#endif
