/* Witness: an old consumer binding the result to v1's type.
     - Built against v1, run against v1 or v2: prints both names, exit 0
       (the symbol, register and pointer are unchanged).
     - Rebuilt against v2: `const char **p = mylib::get_names();` is
       rejected -- char ** does not convert to const char **. */
#include "v1.h"
#include <cstdio>
#include <cstring>

int main() {
    const char **p = mylib::get_names();
    std::printf("%s %s\n", p[0], p[1]);
    return std::strcmp(p[1], "beta") == 0 ? 0 : 1;
}
