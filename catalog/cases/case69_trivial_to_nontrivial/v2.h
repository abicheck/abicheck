#ifndef POINT_H
#define POINT_H

/* v2: user-defined destructor makes Point NON-trivially copyable.
   On Itanium ABI, non-trivial types are passed via hidden pointer
   instead of registers — the caller allocates stack space and passes
   a pointer in %rdi. This is an invisible calling-convention break. */
struct Point {
    double x;
    double y;
    ~Point() {}  /* makes the struct non-trivially copyable */
};

/* Point is now passed via hidden pointer in %rdi (non-trivial) */
double distance(struct Point a, struct Point b);

#endif
