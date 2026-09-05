#include "v2.h"
double matrix_get(const Matrix *m, int r, int c) { return m->data[r][c]; }
void   matrix_set(Matrix *m, int r, int c, double val) { m->data[r][c] = val; }
