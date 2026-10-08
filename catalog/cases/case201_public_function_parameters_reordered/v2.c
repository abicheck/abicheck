#include "v2.h"

/* Record what the callee actually received, so a witness can check argument
   delivery instead of relying on an implementation that ignores it. */
static int    last_index;
static double last_value;

void plot_point(double value, int index) { last_index = index; last_value = value; }
void plot_reset(void)                    { last_index = 0; last_value = 0.0; }
int    plot_last_index(void)             { return last_index; }
double plot_last_value(void)             { return last_value; }
