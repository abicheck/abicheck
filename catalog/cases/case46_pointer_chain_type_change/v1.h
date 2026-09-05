/* case46: Pointer chain — pointed-to type changes through indirection levels
 *
 * A function returns int** (pointer to row pointers). The ultimate pointee type
 * changes from int to long in v2. Callers dereferencing the chain observe
 * incompatible element type/layout.
 *
 * BREAKING: FUNC_RETURN_CHANGED, PARAM_TYPE_CHANGED
 */
#ifndef CASE46_V1_H
#define CASE46_V1_H

int **get_matrix(void);
void  set_cell(int *const *matrix, int row, int col, int val);
int   sum_row(int *const *matrix, int row, int cols);

#endif
