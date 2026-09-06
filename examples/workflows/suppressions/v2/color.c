#include "color.h"

RGB convert_to_rgb(int hex) {
    RGB c = {(hex >> 16) & 0xFF, (hex >> 8) & 0xFF, hex & 0xFF};
    return c;
}
