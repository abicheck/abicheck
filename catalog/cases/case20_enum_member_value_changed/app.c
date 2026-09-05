#include "old/lib.h"
#include <stdio.h>

int main(void) {
    int r = get_result();
    if (r == ERROR) {
        printf("Error detected (correct)\n");
        return 0;
    }

    printf("WRONG RESULT: expected ERROR(%d), got %d\n", ERROR, r);
    return 1;
}
