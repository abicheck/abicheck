/* DEMO: the consumer compiles against the unchanged declaration and is
   unaffected by the implementation's internal qualifier. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    float dst[4] = {1, 2, 3, 4};
    float src[4] = {10, 20, 30, 40};
    blend(dst, src, 4);
    for (int i = 0; i < 4; i++) printf("%.1f ", dst[i]);
    printf("\n");
    return 0;
}
