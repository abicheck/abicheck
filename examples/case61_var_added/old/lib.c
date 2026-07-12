/* bad.c — v1: library exports one global variable and a function. */
int lib_version = 1;

int get_version(void) { return lib_version; }
