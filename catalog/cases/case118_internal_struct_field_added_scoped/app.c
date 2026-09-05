#include "v1.h"
#include <stdio.h>
int main(void) {
    Point p = {1, 2};
    Point q = translate(p, 10, 20);
    printf("translate -> (%d, %d)\n", q.x, q.y);
    return 0;
}
