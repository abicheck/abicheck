/* case45: Multi-dimensional array element type / dimension change
 *
 * A struct member is a multi-dimensional array. Its inner type changes
 * (or dimensions change), breaking layout.
 *
 * BREAKING: TYPE_SIZE_CHANGED, TYPE_FIELD_TYPE_CHANGED
 * libabigail equivalent: Subrange_Change in multi-dim array
 */
#ifndef CASE45_V1_H
#define CASE45_V1_H

#define ROWS 4
#define COLS 4

typedef struct Matrix {
    float data[ROWS][COLS];   /* 4×4 = 16 floats = 64 bytes */
    int   rows;
    int   cols;
} Matrix;

float matrix_get(const Matrix *m, int r, int c);
void  matrix_set(Matrix *m, int r, int c, float val);

#endif
