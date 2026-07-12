/* good.c — v2: new global variable lib_build_number added.
   Existing symbols unchanged — purely additive. */
int lib_version = 2;
int lib_build_number = 1042;

int get_version(void) { return lib_version; }
