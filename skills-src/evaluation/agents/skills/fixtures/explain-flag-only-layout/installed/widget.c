#include "widget.h"
int widget_area(const struct widget *w) {
    int area = w->width * w->height;
    return w->kind == WIDGET_FRAMED ? area + 2 * (w->width + w->height) : area;
}
