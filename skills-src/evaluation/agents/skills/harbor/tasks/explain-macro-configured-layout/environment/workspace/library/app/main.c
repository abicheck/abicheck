#include <stdio.h>
#include "widget.h"
struct slot { struct widget w; int spare[4]; };
int main(void) {
    struct slot s = { { "panel", 3, 4 }, { 0, 0, 1, 1 } };
    printf("area=%d\n", widget_area(&s.w));
    return 0;
}
