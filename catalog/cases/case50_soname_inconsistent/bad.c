/* bad.c — library has SONAME set to libfoo.so.0, but the actual
   version is 1.x — SONAME major does not match the release version. */
int foo(void) { return 42; }
int bar(int x) { return x + 1; }
