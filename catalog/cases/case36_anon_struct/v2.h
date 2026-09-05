/* case36 v2: Anonymous union member type changed */
#ifndef V2_H
#define V2_H

#ifdef __cplusplus
extern "C" {
#endif

struct Variant {
    int tag;
    union {
        int    i;
        double d;    /* was float f → now double d (size: 4 → 8 bytes) */
    };
};

int variant_get_int(const struct Variant *v);

#ifdef __cplusplus
}
#endif
#endif
