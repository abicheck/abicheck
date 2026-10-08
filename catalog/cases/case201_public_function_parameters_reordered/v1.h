#ifndef CASE201_H
#define CASE201_H

void plot_point(int index, double value);
void plot_reset(void);

/* What the last plot_point() call received. */
int    plot_last_index(void);
double plot_last_value(void);

#endif
