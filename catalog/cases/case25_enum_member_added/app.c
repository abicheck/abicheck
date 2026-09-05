#include "old/lib.h"
#include <stdio.h>

int main(void) {
    enum Color c = get_color();
    switch (c) {
        case RED:   printf("RED\n"); break;
        case GREEN: printf("GREEN\n"); break;
        case BLUE:  printf("BLUE\n"); break;
        default:    printf("UNKNOWN: %d\n", (int)c); break;
    }
    return 0;
}
