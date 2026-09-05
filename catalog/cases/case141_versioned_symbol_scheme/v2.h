/* v2 bumps the whole API to major 4: every exported symbol's version suffix
 * goes `_3` -> `_4`. Source that uses the version-macro keeps compiling, but the
 * shipped .so renames all symbols at once — a library-wide versioned-symbol
 * scheme, not independent API removals. */
int mylib_init_4(int x);
int mylib_open_4(int x);
int mylib_read_4(int x);
int mylib_write_4(int x);
int mylib_close_4(int x);
int mylib_flush_4(int x);
