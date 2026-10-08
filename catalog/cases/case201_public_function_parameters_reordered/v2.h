#ifndef CASE201_H
#define CASE201_H

/* The two parameters swapped places. The exported C symbol name is
 * unchanged, so the link still succeeds. Whether an old caller's arguments
 * still arrive depends on the ABI: x86-64 System V assigns the int and the
 * double to separate register files regardless of position (delivery stays
 * intact), while positional ABIs (Microsoft x64, i386) swap them. The
 * declarations are incompatible either way.
 */
void plot_point(double value, int index);
void plot_reset(void);

/* What the last plot_point() call received. */
int    plot_last_index(void);
double plot_last_value(void);

#endif
