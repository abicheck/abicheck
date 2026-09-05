/* good.h — v2: functions moved to header as static inline.
   The symbols are no longer exported from the shared library. */
#ifndef MYLIB_H
#define MYLIB_H

static inline int fast_abs(int x) { return x < 0 ? -x : x; }
static inline int fast_max(int a, int b) { return a > b ? a : b; }

#endif
