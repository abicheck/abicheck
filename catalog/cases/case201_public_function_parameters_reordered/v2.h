#ifndef CASE201_H
#define CASE201_H

/* The two parameters swapped places. The exported C symbol name is
 * unchanged, so the link still succeeds -- but `index` and `value` are
 * passed in different register classes, so an old caller's integer lands
 * where the callee expects a double and vice versa.
 */
void plot_point(double value, int index);
void plot_reset(void);

#endif
