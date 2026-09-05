/* good.h — enum now has a value that forces a wider underlying type.
   The large sentinel value (0x100000000LL) exceeds INT_MAX, so the
   compiler must use a 64-bit underlying type.
   In C++, you could use `enum Color : long` explicitly. */
#ifndef MYLIB_H
#define MYLIB_H

typedef enum {
    COLOR_RED   = 0,
    COLOR_GREEN = 1,
    COLOR_BLUE  = 2,
    _COLOR_FORCE_64BIT = 0x100000000LL,  /* forces underlying type to long */
} Color;

typedef struct {
    Color color;    /* sizeof(long) = 8 on LP64 */
    int   alpha;
} Pixel;

Pixel* pixel_create(Color c, int alpha);
void pixel_destroy(Pixel *p);
Color pixel_get_color(const Pixel *p);

#endif
