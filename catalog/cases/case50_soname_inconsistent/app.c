#include <stdio.h>

extern int foo(void);
extern int bar(int x);

int main(void) {
    printf("foo() = %d\n", foo());
    printf("bar(5) = %d\n", bar(5));
    return 0;
}
