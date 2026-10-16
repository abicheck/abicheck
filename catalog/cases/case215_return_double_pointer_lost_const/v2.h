#ifndef CASE215_H
#define CASE215_H

namespace mylib {
/* The inner pointee lost const. The pointer value and register are the same,
 * so prebuilt callers keep working; but char ** does not convert to
 * const char ** in C++ ([conv.qual]: no const at the intermediate level), so
 * `const char **p = mylib::get_names();` no longer compiles. */
char **get_names();
}

#endif
