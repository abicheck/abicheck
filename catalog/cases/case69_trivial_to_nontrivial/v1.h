#ifndef POINT_H
#define POINT_H

/* v1: trivially copyable — passed in registers on x86-64 Itanium ABI.
   No user-defined destructor, copy/move ctor, or copy/move assignment. */
struct Point {
    double x;
    double y;
};

/* Point is passed by value in %xmm0/%xmm1 (trivially copyable, fits 2 regs) */
double distance(struct Point a, struct Point b);

#endif
