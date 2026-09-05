#ifndef CASE121_TOOLCHAIN_FLAG_DRIFT_H
#define CASE121_TOOLCHAIN_FLAG_DRIFT_H

/* Public surface is identical between v1 and v2. The only difference is the
 * set of ABI-affecting compiler flags used to build the shared library. */
int add(int a, int b);
double scale(double x);

#endif /* CASE121_TOOLCHAIN_FLAG_DRIFT_H */
