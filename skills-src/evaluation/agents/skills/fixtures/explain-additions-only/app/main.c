#include <stdio.h>
#include "widget.h"
int main(void) {
    int h = widget_create(3);
    printf("size=%d\n", widget_resize(h, 4));
    return 0;
}
