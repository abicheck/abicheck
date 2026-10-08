/* good.c — use -fvisibility=hidden + explicit default for public symbols.
   This is the industrial pattern (Qt, GCC, etc.) for ELF symbol visibility.
   Both helpers keep their definitions and external linkage; only their ELF
   visibility changes, so this is a pure visibility mutation (no removal). */
__attribute__((visibility("default"))) int public_api(int x) { return x; }
__attribute__((visibility("hidden")))  int internal_helper(int x) { return x * 2; }
__attribute__((visibility("hidden")))  int another_impl(int x) { return x + 3; }
