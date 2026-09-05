#include "v1.h"
float matrix_get(const Matrix *m, int r, int c) { return m->data[r][c]; }
void  matrix_set(Matrix *m, int r, int c, float val) { m->data[r][c] = val; }
