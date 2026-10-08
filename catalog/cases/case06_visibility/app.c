/* An old consumer that (incorrectly) resolved the accidentally exported
   helpers at link time.  It is linked against libv1 only; the harness then
   substitutes libv2 under the same name.
     v1: both helpers resolve           -> prints the results, exits 0
     v2: helpers are hidden             -> the dynamic loader refuses to start
                                           the program (undefined symbol),
                                           a non-zero exit before main(). */
#include <stdio.h>

int public_api(int x);
int internal_helper(int x);
int another_impl(int x);

int main(void) {
    int a = public_api(1);
    int b = internal_helper(2);
    int c = another_impl(3);
    printf("public_api(1)=%d internal_helper(2)=%d another_impl(3)=%d\n", a, b, c);
    if (a != 1 || b != 4 || c != 6) {
        fprintf(stderr, "unexpected helper results\n");
        return 1;
    }
    return 0;
}
