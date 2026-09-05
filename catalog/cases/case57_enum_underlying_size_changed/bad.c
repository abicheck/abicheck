/* bad.c — v1: Color enum with values that fit in int. */
#include "bad.h"
#include <stdlib.h>

Pixel* pixel_create(Color c, int alpha) {
    Pixel *p = malloc(sizeof(Pixel));
    p->color = c;
    p->alpha = alpha;
    return p;
}

void pixel_destroy(Pixel *p) { free(p); }
Color pixel_get_color(const Pixel *p) { return p->color; }
