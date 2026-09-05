#ifndef CASE116_V2_H
#define CASE116_V2_H

/* v2: the same public field gains the C11 _Atomic qualifier. Per WG14 this
 * may change the size/alignment and calling-convention treatment, so it is an
 * ABI break even though the underlying integer type is unchanged. */
struct counter {
    _Atomic int value;
};

long get_count(const struct counter *c);

#endif /* CASE116_V2_H */
