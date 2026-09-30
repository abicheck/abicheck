#include <stdio.h>
#include "widget.h"
struct pair { struct widget w; int guard; };
int main(void) {
    struct pair p = { { 3, 4 }, 1000 };
    printf("area=%d\n", widget_area(&p.w));
    return 0;
}
