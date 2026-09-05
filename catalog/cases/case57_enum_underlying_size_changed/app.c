#include <stdio.h>

/* App compiled against v1: Color is int-sized (4 bytes) */
typedef enum { COLOR_RED = 0, COLOR_GREEN = 1, COLOR_BLUE = 2 } Color;
typedef struct { Color color; int alpha; } Pixel;

extern Pixel* pixel_create(Color c, int alpha);
extern void pixel_destroy(Pixel *p);
extern Color pixel_get_color(const Pixel *p);

int main(void) {
    Pixel *p = pixel_create(COLOR_BLUE, 255);
    int color = (int)pixel_get_color(p);
    int alpha = p->alpha;  /* direct v1-layout read */

    printf("color = %d\n", color);
    printf("alpha = %d\n", alpha);

    int ok = (color == COLOR_BLUE) && (alpha == 255);
    pixel_destroy(p);

    if (!ok) {
        printf("WRONG RESULT: enum underlying size/layout changed\n");
        return 1;
    }
    return 0;
}
