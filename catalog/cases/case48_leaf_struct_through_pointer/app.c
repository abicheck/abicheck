#include "v1.h"
#include <stdio.h>

struct Wrapped {
    Container c;
    int guard;
};

int main(void) {
    struct Wrapped w;
    w.guard = 0x12345678;

    container_init(&w.c, 9, 11, 22);

    short x = 0, y = 0;
    container_get_pos(&w.c, &x, &y);
    int f = container_flags(&w.c);

    printf("pos=(%d,%d) flags=%d guard=0x%x\n", (int)x, (int)y, f, w.guard);
    printf("expected: pos=(11,22) flags=0 guard=0x12345678\n");

    if (x != 11 || y != 22 || f != 0 || w.guard != 0x12345678) {
        printf("CORRUPTION: nested leaf layout changed and overwrote caller memory\n");
        return 1;
    }
    return 0;
}
