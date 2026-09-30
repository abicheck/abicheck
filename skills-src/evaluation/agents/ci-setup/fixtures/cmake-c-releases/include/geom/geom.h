#ifndef GEOM_H
#define GEOM_H
typedef struct geom_point { double x; double y; } geom_point;
double geom_distance(const geom_point *a, const geom_point *b);
int geom_version(void);
#endif
