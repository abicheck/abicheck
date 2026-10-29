#ifndef ADV_API_H
#define ADV_API_H
#include <stddef.h>
typedef struct point { int x; int y; } point;
int add_values(int a, int b);
int point_sum(const point *p);
#endif
