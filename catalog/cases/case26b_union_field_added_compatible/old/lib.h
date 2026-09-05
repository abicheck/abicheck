#ifndef LIB_H
#define LIB_H

/* v1: union dominated by double (8 bytes); sizeof(Value) == 8 */
union Value {
    long   l;
    double d;
};

void fill(union Value* v);

#endif /* LIB_H */
