/* bad.h — enum fits in int (default underlying type). */
#ifndef MYLIB_H
#define MYLIB_H

typedef enum {
    COLOR_RED   = 0,
    COLOR_GREEN = 1,
    COLOR_BLUE  = 2,
} Color;

typedef struct {
    Color color;    /* sizeof(int) = 4 on most platforms */
    int   alpha;
} Pixel;

Pixel* pixel_create(Color c, int alpha);
void pixel_destroy(Pixel *p);
Color pixel_get_color(const Pixel *p);

#endif
