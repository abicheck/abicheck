/* bad.c — v1: library exports a global variable and functions. */
int lib_version = 1;
int lib_debug_level = 0;

int get_version(void) { return lib_version; }
int get_debug(void) { return lib_debug_level; }
