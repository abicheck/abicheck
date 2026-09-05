#include "v1.h"

static int storage[4][4];
static int *row_ptrs[4] = {storage[0], storage[1], storage[2], storage[3]};

int **get_matrix(void) { return row_ptrs; }

void set_cell(int *const *matrix, int row, int col, int val) {
    matrix[row][col] = val;
}

int sum_row(int *const *matrix, int row, int cols) {
    int s = 0;
    for (int c = 0; c < cols; c++) s += matrix[row][c];
    return s;
}
