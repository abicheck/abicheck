#ifndef WIDGET_NAME_MAX
#define WIDGET_NAME_MAX 16
#endif
struct widget {
    char name[WIDGET_NAME_MAX];
    int width;
    int height;
};
int widget_area(const struct widget *w);
