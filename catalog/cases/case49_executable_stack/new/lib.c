/* good.c — same library, built purely from C without any assembly
   that would request an executable stack. GNU_STACK is RW (not RWX). */
int compute(int x) { return x * x + 1; }
int transform(int x, int y) { return x + y * 2; }
