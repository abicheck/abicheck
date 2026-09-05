/* case36: Anonymous struct/union field changes
 *
 * Binary ABI break: changing the type of an anonymous union member
 * changes the containing struct's layout.
 *
 * abicheck detects: ANON_FIELD_CHANGED
 * ABICC/abidiff equivalent: test44, test45 (anonymous struct diffs)
 */
#ifndef V1_H
#define V1_H

#ifdef __cplusplus
extern "C" {
#endif

struct Variant {
    int tag;
    union {          /* anonymous union */
        int    i;
        float  f;    /* changed to double in v2 → size grows */
    };
};

int variant_get_int(const struct Variant *v);

#ifdef __cplusplus
}
#endif
#endif
