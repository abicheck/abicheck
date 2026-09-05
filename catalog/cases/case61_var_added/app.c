#include <stdio.h>

extern int lib_version;
extern int get_version(void);

int main(void) {
    printf("version = %d\n", lib_version);
    printf("get_version() = %d\n", get_version());
    return 0;
}
