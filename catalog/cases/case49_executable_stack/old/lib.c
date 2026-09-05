/* bad.c — library linked with -Wl,-z,execstack, giving it an executable
   GNU_STACK segment (RWE). This disables NX protection for the process. */
int compute(int x) { return x * x + 1; }
int transform(int x, int y) { return x + y * 2; }
