/* case45 v2: array element type changed float→double — BREAKING */
#ifndef CASE45_V2_H
#define CASE45_V2_H

#define ROWS 4
#define COLS 4

typedef struct Matrix {
    double data[ROWS][COLS];  /* 4×4 = 16 doubles = 128 bytes */
    int    rows;
    int    cols;
} Matrix;

double matrix_get(const Matrix *m, int r, int c);
void   matrix_set(Matrix *m, int r, int c, double val);

#endif
