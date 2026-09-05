#include "old/lib.h"
#include <stdio.h>

int main(void) {
    enum Status s = get_status();
    if (s == FOO) {
        printf("FOO\n");
        return 0;
    }

    printf("WRONG RESULT: expected FOO(%d), got %d\n", FOO, (int)s);
    return 1;
}
