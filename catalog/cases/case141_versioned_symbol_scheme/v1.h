/* Public C API that carries the library MAJOR version as a name suffix — the
 * ICU `u_<name>_<major>` convention. v1 = major 3. Consumers are expected to
 * spell the unsuffixed name via a macro; the exported symbol is suffixed. */
int mylib_init_3(int x);
int mylib_open_3(int x);
int mylib_read_3(int x);
int mylib_write_3(int x);
int mylib_close_3(int x);
int mylib_flush_3(int x);
