/* Exported ABI surface — identical in v1 and v2. Dependency metadata
   differs: v2 force-links libm (--no-as-needed -lm), adding a DT_NEEDED
   entry, but the exported symbols and their types are unchanged. */
int compute(int x) { return x * x + 1; }
int transform(int x, int y) { return x + y * 2; }
