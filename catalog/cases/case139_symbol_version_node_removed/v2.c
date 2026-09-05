/* v1: alpha is bound to version node LIBX_1.0, beta to LIBX_2.0.
   A consumer that links against beta records a dependency on beta@LIBX_2.0. */
int alpha(int x) { return x + 1; }
int beta(int x) { return x + 2; }
