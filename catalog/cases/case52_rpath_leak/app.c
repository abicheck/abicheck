#include <stdio.h>

extern int encode(const char *input);
extern int decode(int code);

int main(void) {
    int h = encode("hello");
    printf("encode(\"hello\") = %d\n", h);
    printf("decode(%d) = %d\n", h, decode(h));
    return 0;
}
