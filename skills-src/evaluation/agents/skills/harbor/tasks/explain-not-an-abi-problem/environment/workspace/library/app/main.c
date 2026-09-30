#include <stdio.h>
#include <stdlib.h>
#include "widget.h"
int main(int argc, char **argv) {
    int delta = argc > 1 ? atoi(argv[1]) : -4;
    int h = widget_create(3);
    int r = widget_resize(h, delta);
    if (r < 0) {
        fprintf(stderr, "resize failed\n");
        return 1;
    }
    printf("size=%d\n", r);
    return 0;
}
