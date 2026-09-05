/* added field 'z' — layout change, callers pass wrong-sized structs */
struct Point { int x; int y; int z; };
int get_x(struct Point *p) { return p->x; }
/* v2: writes x=1, y=2, AND z=3 — but caller only allocated 8 bytes (no z)! */
void init_point(struct Point *p) { p->x = 1; p->y = 2; p->z = 3; }
