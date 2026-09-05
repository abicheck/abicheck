#ifndef CASE116_V1_H
#define CASE116_V1_H

/* v1: a public struct field uses a plain (unqualified) int. */
struct counter {
    int value;
};

long get_count(const struct counter *c);

#endif /* CASE116_V1_H */
