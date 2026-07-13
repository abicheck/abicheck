#include <stdio.h>

extern int dispatch(int which, int a, int b);

int main(void) {
    printf("dispatch(0, 5, 3) = %d (add, expected 8)\n", dispatch(0, 5, 3));
    printf("dispatch(1, 5, 3) = %d (sub, expected 2)\n", dispatch(1, 5, 3));
    return 0;
}
