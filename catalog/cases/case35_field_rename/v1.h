/* case35: Field rename — same offset and type, different name
 *
 * Source-level break: code referencing old field name won't compile.
 * Binary layout unchanged (same offset, same type).
 *
 * abicheck detects: FIELD_RENAMED (via offset+type matching heuristic)
 * ABICC equivalent: Renamed_Field
 */
#ifndef V1_H
#define V1_H

#ifdef __cplusplus
extern "C" {
#endif

struct Point {
    int x;      /* renamed to col in v2 */
    int y;      /* renamed to row in v2 */
};

struct Point make_point(int a, int b);

#ifdef __cplusplus
}
#endif
#endif
