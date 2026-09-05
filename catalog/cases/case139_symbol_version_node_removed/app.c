#include <stdio.h>

extern int alpha(int x);
extern int beta(int x);

int main(void) {
    printf("alpha(1) = %d\n", alpha(1));
    printf("beta(1) = %d\n", beta(1));
    return 0;
}
