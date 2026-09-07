#ifndef CASE202_H
#define CASE202_H

/* Only the *declaration* order changed -- plot_reset() is now listed first.
 * No parameter list, type, or export changed, so there is nothing here for
 * a consumer to notice.
 */
void plot_reset(void);
void plot_point(int index, double value);

#endif
