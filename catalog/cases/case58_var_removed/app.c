#include <stdio.h>

extern int lib_version;
extern int lib_debug_level;
extern int get_version(void);
extern int get_debug(void);

int main(void) {
    printf("version = %d\n", lib_version);
    lib_debug_level = 3;  /* v2: symbol gone → linker error or crash */
    printf("debug = %d\n", get_debug());
    return 0;
}
