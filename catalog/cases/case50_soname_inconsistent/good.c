/* good.c — library has SONAME correctly set to libfoo.so.1,
   matching the release version / ABI epoch. */
int foo(void) { return 42; }
int bar(int x) { return x + 1; }
