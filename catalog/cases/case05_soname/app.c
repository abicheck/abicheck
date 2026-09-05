#include <stdio.h>

/* Link against libbad.so or libgood.so — both export foo() */
int foo(void);

int main(void) {
    printf("foo() = %d\n", foo());
    return 0;
}
