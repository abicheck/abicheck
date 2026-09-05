#ifndef LIB_H
#define LIB_H

/* v2: int i added — sizeof stays 8 (int 4 < double 8); no ABI break */
union Value {
    long   l;
    double d;
    int    i;   /* new: smaller than max member, sizeof unchanged */
};

void fill(union Value* v);

#endif /* LIB_H */
