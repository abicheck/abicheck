#include "v2.h"

static long storage[4][4];
static long *row_ptrs[4] = {storage[0], storage[1], storage[2], storage[3]};

long **get_matrix(void) { return row_ptrs; }

void set_cell(long *const *matrix, int row, int col, long val) {
    matrix[row][col] = val;
}

long sum_row(long *const *matrix, int row, int cols) {
    long s = 0;
    for (int c = 0; c < cols; c++) s += matrix[row][c];
    return s;
}
