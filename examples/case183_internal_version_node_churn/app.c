#include <stdio.h>

extern int public_api(int x);

int main(void) {
    printf("public_api(5) -> %d\n", public_api(5));
    return 0;
}
