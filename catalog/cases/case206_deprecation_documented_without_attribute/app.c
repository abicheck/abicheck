/* DEMO: the consumer still calls legacy_open() and gets no warning of any
   kind -- the deprecation exists only in a comment. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    printf("legacy_open -> %d\n", legacy_open("demo"));
    printf("modern_open -> %d\n", modern_open("demo"));
    return 0;
}
