#include <stdio.h>

extern int compute(int x);
extern int transform(int x, int y);

int main(void) {
    printf("compute(7) = %d\n", compute(7));
    printf("transform(3, 4) = %d\n", transform(3, 4));
    return 0;
}
