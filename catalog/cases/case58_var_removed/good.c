/* good.c — v2: lib_debug_level global variable removed.
   The function get_debug() still exists but uses a static variable. */
int lib_version = 2;
static int debug_level = 0;

int get_version(void) { return lib_version; }
int get_debug(void) { return debug_level; }
