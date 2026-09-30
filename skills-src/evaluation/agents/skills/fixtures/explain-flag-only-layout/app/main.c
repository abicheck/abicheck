#include <stdio.h>
#include "widget.h"
int main(void) {
    struct widget w = { WIDGET_PLAIN, 3, 4 };
    printf("area=%d\n", widget_area(&w));
    return 0;
}
