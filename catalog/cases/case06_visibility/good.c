/* good.c — use -fvisibility=hidden + explicit default for public symbols.
   This is the industrial pattern (Qt, GCC, etc.) for ELF symbol visibility. */
__attribute__((visibility("default"))) int public_api(int x) { return x; }
__attribute__((visibility("hidden")))  int internal_helper(int x) { return x * 2; }
