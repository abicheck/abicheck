#include <stdio.h>

/* App compiled against v1: expects fast_abs and fast_max as library symbols */
extern int fast_abs(int x);
extern int fast_max(int a, int b);

int main(void) {
    printf("abs(-7) = %d\n", fast_abs(-7));
    printf("max(3, 9) = %d\n", fast_max(3, 9));
    /* v1: symbols resolved from .so → works */
    /* v2: symbols gone → runtime linker error */
    return 0;
}
