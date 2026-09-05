#include <stdio.h>

extern void process(char *out, const char *in);
extern int compute(int x);

int main(void) {
    char out[128];
    process(out, "hello");
    printf("process -> %s\n", out);
    printf("compute(7) = %d\n", compute(7));
    return 0;
}
